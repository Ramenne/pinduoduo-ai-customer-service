import hashlib
import json


def metadata(app, **changes):
    p=app.state.settings.apk_path
    value={'package_name':'com.feifei.phonebridge','version_code':37,
           'version_name':app.state.settings.apk_version,'size_bytes':p.stat().st_size,
           'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}|changes
    p.with_suffix('.update.json').write_text(json.dumps(value))
    return value


def test_update_missing_does_not_break_old_download(client):
    assert client.get('/api/v1/app-update').status_code==404
    assert client.get('/download/apk').status_code==200


def test_update_manifest_and_digest_pinned_download(client,app):
    value=metadata(app)
    response=client.get('/api/v1/app-update')
    assert response.status_code==200,response.text
    assert response.json()==value
    assert response.headers['cache-control']=='no-store'
    downloaded=client.get('/download/apk',params={'sha256':value['sha256']})
    assert hashlib.sha256(downloaded.content).hexdigest()==value['sha256']
    assert client.get('/download/apk',params={'sha256':'0'*64}).status_code==409


def test_update_rejects_metadata_mismatch_and_oversized_metadata(client,app):
    for change in [{'version_code':True},{'sha256':'0'*64},{'version_name':'wrong'},
                   {'package_name':'other.app'},{'size_bytes':0}]:
        metadata(app,**change)
        assert client.get('/api/v1/app-update').status_code==503
    app.state.settings.apk_path.with_suffix('.update.json').write_bytes(b' '*8193)
    assert client.get('/api/v1/app-update').status_code==503
