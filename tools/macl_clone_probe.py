#!/usr/bin/env python3
"""Issue #5 diagnostic probe for the clonefile permission-preservation adapter.

Measures how the kernel treats four ways of preparing a file inside the
TCC-protected draft root, printing ONLY sanitized JSON: attribute names, byte
lengths, presence and equality booleans. Raw attribute values, UUIDs and index
content never leave process memory.

Safety contract:
- Creates only new hidden fixtures whose names carry a random tag; files are
  0600, directories 0700, everything O_EXCL. Nothing is deleted; fixture paths
  are printed so the user can move exactly these to the Trash afterwards.
- The real home index is used only as a read-only clone/copy source. It is
  never written, truncated, chmodded or renamed.
- Extended attributes are only READ via /usr/bin/xattr. No attribute writes or
  deletes, no sudo, no SIP/TCC changes, no network.
- EPERM / ELOOP / unexpected directory shape stops the affected branch; the
  probe never forces its way through a permission prompt.

Steps: P1 direct create; P2 clonefile of the home index then an in-place
O_TRUNC overwrite; P3 create inside a new subdirectory then os.replace into
the draft root; P4 copyfile(3) (only when P2 and P3 both carry macl).
"""
import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import secrets
import stat
import subprocess

KNOWN = ('com.apple.macl', 'com.apple.provenance', 'com.apple.quarantine')
REWRITE_BYTES = b'probe-overwrite\n'
LIBC = ctypes.CDLL(None, use_errno=True)


def safe_directory(path):
    path = Path(os.path.abspath(path))
    for part in reversed((path, *path.parents)):
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise OSError(errno.ELOOP, 'directory rejected')
    return path


def read_attrs(path):
    listing = subprocess.run(['/usr/bin/xattr', str(path)], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=15, check=False)
    if listing.returncode != 0:
        raise OSError(errno.EIO, 'attribute list blocked')
    out = {}
    for name in listing.stdout.decode('utf-8').splitlines():
        if name in KNOWN:
            value = subprocess.run(['/usr/bin/xattr', '-px', name, str(path)],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=15, check=False)
            if value.returncode != 0:
                raise OSError(errno.EIO, 'attribute read blocked')
            out[name] = bytes.fromhex(value.stdout.decode('ascii'))
        else:
            out[name] = None  # Unknown attributes are counted, never read.
    return out


def describe(attrs):
    return {'known_attributes': {k: len(attrs[k]) for k in KNOWN if k in attrs},
            'other_attribute_count': sum(1 for k in attrs if k not in KNOWN)}


def compare(source, target):
    return {k: {'source_present': k in source, 'target_present': k in target,
                'same': source[k] == target[k] if k in source and k in target else None}
            for k in KNOWN if k in source or k in target}


def new_file(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rewrite(path):
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    try:
        os.write(fd, REWRITE_BYTES)
        os.fsync(fd)
    finally:
        os.close(fd)


def clonefile(src, dst):
    fn = LIBC.clonefile
    fn.argtypes, fn.restype = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint], ctypes.c_int
    if fn(os.fsencode(src), os.fsencode(dst), 0):
        raise OSError(ctypes.get_errno(), 'clonefile failed')


def copyfile_full(src, dst):
    fn = LIBC.copyfile
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    # COPYFILE_DATA (8) | COPYFILE_XATTR (4) | COPYFILE_STAT (2)
    if fn(os.fsencode(src), os.fsencode(dst), None, 8 | 4 | 2):
        raise OSError(ctypes.get_errno(), 'copyfile failed')


def has_macl(desc):
    return 'com.apple.macl' in desc['known_attributes']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft-root',
                        default=str(Path.home() / 'Movies/JianyingPro/User Data/Projects/com.lveditor.draft'))
    args = parser.parse_args()
    draft_root = Path(args.draft_root)
    index = draft_root / 'root_meta_info.json'
    report = {'schema': 'macl-clone-probe/v1', 'fixtures_created': [], 'fixtures_retained': True,
              'attribute_values_published': False, 'real_index_written': False, 'steps': {}}
    try:
        safe_directory(draft_root)
        info = index.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise OSError(errno.ELOOP, 'home index rejected')
    except OSError as exc:
        report['aborted'] = {'reason': 'directory shape check failed', 'errno': exc.errno}
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 1

    report['macos'] = platform.mac_ver()[0]
    tag = secrets.token_hex(4)
    report['tag'] = tag

    def fixture(name):
        return draft_root / ('.macl-probe-' + tag + '-' + name)

    try:
        path = fixture('p1.tmp')
        new_file(path)
        report['fixtures_created'].append(str(path))
        p1_desc = describe(read_attrs(path))
        report['steps']['P1_direct_create'] = {'status': 'ok', 'direct_file': p1_desc}
    except Exception as exc:  # noqa: BLE001 - stop the whole probe
        report['steps']['P1_direct_create'] = {'status': 'blocked', 'errno': getattr(exc, 'errno', None),
                                               'note': type(exc).__name__}
        report['stopped'] = 'P1 blocked; remaining steps skipped'
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 1
    if not has_macl(p1_desc):
        report['stopped'] = ('P1 unexpected: direct create produced no com.apple.macl; '
                             'environment differs from the affected case, stop')
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 1

    p2_desc = p3_desc = None
    try:
        path = fixture('p2.tmp')
        source = read_attrs(index)
        clonefile(index, path)
        os.chmod(path, 0o600)
        report['fixtures_created'].append(str(path))
        after_clone = read_attrs(path)
        rewrite(path)
        after_rewrite = read_attrs(path)
        p2_desc = describe(after_rewrite)
        report['steps']['P2_clonefile_then_overwrite'] = {
            'status': 'ok', 'method': 'clonefile(2)', 'source_index': describe(source),
            'clone_after_clone': describe(after_clone), 'clone_vs_source': compare(source, after_clone),
            'clone_after_overwrite': p2_desc, 'overwrite_vs_source': compare(source, after_rewrite)}
    except Exception as exc:  # noqa: BLE001 - stop this branch only
        report['steps']['P2_clonefile_then_overwrite'] = {
            'status': 'blocked', 'errno': getattr(exc, 'errno', None), 'note': type(exc).__name__}
    try:
        subdir = fixture('dir')
        os.mkdir(subdir, 0o700)
        report['fixtures_created'].append(str(subdir))
        inside = subdir / ('.macl-probe-' + tag + '-inside.tmp')
        new_file(inside)
        inside_desc = describe(read_attrs(inside))
        moved = fixture('p3-moved.tmp')
        os.replace(inside, moved)
        report['fixtures_created'].append(str(moved))
        p3_desc = describe(read_attrs(moved))
        report['steps']['P3_subdir_then_replace'] = {
            'status': 'ok', 'file_inside_new_subdir': inside_desc,
            'file_after_replace_to_root': p3_desc, 'subdir_itself': describe(read_attrs(subdir))}
    except Exception as exc:  # noqa: BLE001
        report['steps']['P3_subdir_then_replace'] = {
            'status': 'blocked', 'errno': getattr(exc, 'errno', None), 'note': type(exc).__name__}

    if p2_desc is not None and p3_desc is not None and has_macl(p2_desc) and has_macl(p3_desc):
        try:
            path = fixture('p4.tmp')
            source = read_attrs(index)
            copyfile_full(index, path)
            os.chmod(path, 0o600)
            report['fixtures_created'].append(str(path))
            after_copy = read_attrs(path)
            rewrite(path)
            after_rewrite = read_attrs(path)
            report['steps']['P4_copyfile_then_overwrite'] = {
                'status': 'ok', 'method': 'copyfile(3) DATA|XATTR|STAT',
                'copy_after_copy': describe(after_copy), 'copy_vs_source': compare(source, after_copy),
                'copy_after_overwrite': describe(after_rewrite),
                'overwrite_vs_source': compare(source, after_rewrite)}
        except Exception as exc:  # noqa: BLE001
            report['steps']['P4_copyfile_then_overwrite'] = {
                'status': 'blocked', 'errno': getattr(exc, 'errno', None), 'note': type(exc).__name__}
    else:
        report['steps']['P4_copyfile_then_overwrite'] = {
            'status': 'skipped', 'reason': 'P2 and P3 result files do not both carry com.apple.macl'}

    ok = all(s.get('status') in {'ok', 'skipped'} for s in report['steps'].values())
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
