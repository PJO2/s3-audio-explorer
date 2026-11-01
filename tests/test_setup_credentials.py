import configparser
import stat

import pytest

from setup_credentials import migrate


def test_migration_preserves_settings_and_existing_profiles(tmp_path):
    config = tmp_path / 'config.py'
    config.write_text('BUCKET = "correct-bucket"\nAWS_PROFILE = None\n'
                      'AWS_ACCESS_KEY_ID = "fake-key"\nAWS_SECRET_ACCESS_KEY = "fake-secret"\n'
                      'AWS_SESSION_TOKEN = "fake-token"\n')
    credentials = tmp_path / '.aws' / 'credentials'
    credentials.parent.mkdir()
    credentials.write_text('[other]\naws_access_key_id = other-key\n')
    migrate(config, credentials)
    settings = {}
    exec(config.read_text(), settings)
    assert settings['BUCKET'] == 'correct-bucket'
    assert settings['AWS_PROFILE'] == 's3-audio-reader'
    assert settings['AWS_ACCESS_KEY_ID'] is None
    assert settings['AWS_SECRET_ACCESS_KEY'] is None
    assert settings['AWS_SESSION_TOKEN'] is None
    parser = configparser.RawConfigParser()
    parser.read(credentials)
    assert parser['s3-audio-reader']['aws_access_key_id'] == 'fake-key'
    assert parser['s3-audio-reader']['aws_secret_access_key'] == 'fake-secret'
    assert parser['s3-audio-reader']['aws_session_token'] == 'fake-token'
    assert parser['other']['aws_access_key_id'] == 'other-key'
    assert stat.S_IMODE(credentials.stat().st_mode) == 0o600
    assert stat.S_IMODE(credentials.parent.stat().st_mode) == 0o700


def test_migration_keeps_keys_if_credentials_write_fails(tmp_path, monkeypatch):
    import setup_credentials
    config = tmp_path / 'config.py'
    original = 'AWS_ACCESS_KEY_ID = "fake-key"\nAWS_SECRET_ACCESS_KEY = "fake-secret"\n'
    config.write_text(original)
    def denied(*args):
        raise PermissionError()
    monkeypatch.setattr(setup_credentials, 'atomic_write', denied)
    with pytest.raises(PermissionError):
        migrate(config, tmp_path / '.aws' / 'credentials')
    assert config.read_text() == original


def test_migration_refuses_to_replace_other_keys(tmp_path):
    config = tmp_path / 'config.py'
    original = 'AWS_ACCESS_KEY_ID = "fake-key"\nAWS_SECRET_ACCESS_KEY = "fake-secret"\n'
    config.write_text(original)
    credentials = tmp_path / 'credentials'
    existing = '[s3-audio-reader]\naws_access_key_id = different-key\n'
    credentials.write_text(existing)
    with pytest.raises(ValueError):
        migrate(config, credentials)
    assert config.read_text() == original
    assert credentials.read_text() == existing
