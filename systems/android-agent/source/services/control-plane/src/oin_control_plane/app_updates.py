"""Public update metadata tied to the currently published APK bytes."""
import hashlib
import json

MAX_APK_BYTES = 32 * 1024 * 1024


def release_info(settings):
    path = settings.apk_path
    if path is None or not path.is_file():
        raise FileNotFoundError('update_not_published')
    size = path.stat().st_size
    if not 1 <= size <= MAX_APK_BYTES:
        raise ValueError('update_size_invalid')
    with path.with_suffix('.update.json').open('rb') as handle:
        raw = handle.read(8193)
    if len(raw) > 8192:
        raise ValueError('update_metadata_too_large')
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {'package_name','version_code','version_name','size_bytes','sha256'}:
        raise ValueError('update_metadata_invalid')
    if (value['package_name'] != 'com.feifei.phonebridge' or
            type(value['version_code']) is not int or not 1 <= value['version_code'] <= 2147483647 or
            value['version_name'] != settings.apk_version or
            type(value['size_bytes']) is not int or value['size_bytes'] != size):
        raise ValueError('update_metadata_mismatch')
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        total = 0
        while block := handle.read(65536):
            total += len(block)
            if total > MAX_APK_BYTES:
                raise ValueError('update_size_invalid')
            digest.update(block)
    if total != size or value['sha256'] != digest.hexdigest():
        raise ValueError('update_digest_mismatch')
    return value
