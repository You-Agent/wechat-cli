"""Bounded local key capture using the audited PR #20 validator. No key bytes in logs."""

import hashlib
import json
import os
import shlex
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

import lldb

sys.path.insert(0, str(Path(__file__).parent))
import codec_capture_lldb as codec

STATE = {}


def atomic_json(path, payload):
    fd, temporary = tempfile.mkstemp(prefix=".capture-", dir=Path(path).parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def report():
    if not STATE:
        return
    atomic_json(
        STATE["report"],
        {
            "pid": STATE["process"].GetProcessID(),
            "breakpoint_locations": STATE.get("locations"),
            "events": STATE["events"],
            "candidate_pairs": len(STATE["seen"]),
            "verified_databases": sorted(STATE["keys"]),
            "missing_required": sorted(STATE["required"] - STATE["keys"].keys()),
            "timed_out": STATE.get("timed_out", False),
            "elapsed_seconds": round(time.monotonic() - STATE["start"], 2),
        },
    )


def on_page(frame, location, internal):
    STATE["events"] += 1
    process = frame.GetThread().GetProcess()
    context = frame.FindRegister("x19").GetValueAsUnsigned()
    keys = frame.FindRegister("x26").GetValueAsUnsigned()
    length = codec._read_memory(process, context + 0x10, 4)
    pointers = codec._read_memory(process, keys + 8, 16)
    if not length or not pointers or struct.unpack("<I", length)[0] != 32:
        return False
    enc_ptr, mac_ptr = struct.unpack("<QQ", pointers)
    if not enc_ptr or not mac_ptr:
        return False
    enc = codec._read_memory(process, enc_ptr, 32)
    mac = codec._read_memory(process, mac_ptr, 32)
    if not enc or not mac:
        return False
    signature = hashlib.sha256(enc + mac).digest()
    if signature in STATE["seen"]:
        return False
    STATE["seen"].add(signature)
    changed = False
    for relative, page in STATE["pages"].items():
        if relative in STATE["keys"]:
            continue
        codec.STATE["page"] = page
        if not codec._valid_pair(enc, mac):
            continue
        STATE["keys"][relative] = {
            "key_type": "sqlcipher_derived_pair",
            "derived_encryption_key": enc.hex(),
            "derived_hmac_key": mac.hex(),
            "salt": page[:16].hex(),
            "page_size": 4096,
            "reserve_size": 80,
            "hmac_algorithm": "sha512",
        }
        changed = True
    if changed:
        atomic_json(STATE["output"], STATE["keys"])
        report()
    complete = STATE["required"].issubset(STATE["keys"])
    return complete


def timeout(process, duration):
    time.sleep(duration)
    if STATE and not STATE["required"].issubset(STATE["keys"]) and process.IsValid():
        STATE["timed_out"] = True
        report()
        process.SendAsyncInterrupt()


def initialize(debugger, command, result, internal):
    args = shlex.split(command)
    if len(args) != 5:
        result.SetError("usage: wx-capture-all OFFSET TARGETS OUTPUT REPORT TIMEOUT")
        return
    offset, targets_file, output, report_file, duration = args
    config = json.loads(Path(targets_file).read_text())
    pages = {}
    for relative in config["files"]:
        with (Path(config["db_dir"]) / relative).open("rb") as stream:
            page = stream.read(4096)
        if len(page) == 4096:
            pages[relative] = page
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    module = None
    loaded = [
        m
        for m in target.module_iter()
        if m.GetFileSpec().GetFilename() == "wechat.dylib"
    ]
    module = next(
        (
            m
            for m in loaded
            if m.GetObjectFileHeaderAddress().GetLoadAddress(target)
            != lldb.LLDB_INVALID_ADDRESS
        ),
        None,
    )
    if not module or not module.IsValid():
        result.SetError("cannot load codec module symbols")
        return
    base = module.GetObjectFileHeaderAddress().GetLoadAddress(target)
    if base != lldb.LLDB_INVALID_ADDRESS:
        breakpoint = target.BreakpointCreateByAddress(base + int(offset, 0))
    else:
        address = module.ResolveFileAddress(int(offset, 0))
        breakpoint = target.BreakpointCreateBySBAddress(address)
    breakpoint.SetScriptCallbackFunction(__name__ + ".on_page")
    process = target.GetProcess()
    verified = {}
    if Path(output).exists():
        for relative, entry in json.loads(Path(output).read_text()).items():
            if relative not in pages:
                continue
            codec.STATE["page"] = pages[relative]
            if codec._valid_pair(
                bytes.fromhex(entry["derived_encryption_key"]),
                bytes.fromhex(entry["derived_hmac_key"]),
            ):
                verified[relative] = entry
    STATE.update(
        pages=pages,
        required=set(config["required"]),
        keys=verified,
        seen=set(),
        output=output,
        report=report_file,
        process=process,
        events=0,
        start=time.monotonic(),
        locations=breakpoint.GetNumLocations(),
    )
    report()
    threading.Thread(
        target=timeout, args=(process, float(duration)), daemon=True
    ).start()
    result.AppendMessage("Read-only database capture armed; no key material is printed")


def show_report(debugger, command, result, internal):
    report()
    result.AppendMessage("Verified databases: {}".format(len(STATE.get("keys", {}))))


def __lldb_init_module(debugger, internal):
    debugger.HandleCommand(
        f"command script add -f {__name__}.initialize wx-capture-all"
    )
    debugger.HandleCommand(
        f"command script add -f {__name__}.show_report wx-capture-report"
    )
