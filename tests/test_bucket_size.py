from datetime import datetime, timezone
from unittest.mock import Mock

import boto3
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber

from s3_audio_explorer import create_app
from s3_audio_explorer.storage import S3Storage, SimulatedStorage, STORAGE_TYPES


def test_cloudwatch_request_and_latest_unsorted_datapoint(monkeypatch):
    monkeypatch.setattr('s3_audio_explorer.storage.STORAGE_TYPES', ('StandardStorage',))
    storage = S3Storage('my-bucket', 'eu-west-3')
    storage.cloudwatch_client = boto3.client(
        'cloudwatch', region_name='eu-west-3', aws_access_key_id='test', aws_secret_access_key='test')
    with Stubber(storage.cloudwatch_client) as stub:
        stub.add_response('get_metric_statistics', {'Datapoints': [
            {'Timestamp': datetime(2026, 11, 1, tzinfo=timezone.utc), 'Average': 2 ** 40},
            {'Timestamp': datetime(2026, 10, 31, tzinfo=timezone.utc), 'Average': 20},
        ]}, {
            'Namespace': 'AWS/S3', 'MetricName': 'BucketSizeBytes',
            'Dimensions': [{'Name': 'BucketName', 'Value': 'my-bucket'},
                           {'Name': 'StorageType', 'Value': 'StandardStorage'}],
            'StartTime': ANY, 'EndTime': ANY, 'Period': 86400,
            'Statistics': ['Average'], 'Unit': 'Bytes',
        })
        expected = {'size': 2 ** 40, 'date': '2026-11-01'}
        assert storage.bucket_size() == expected
        assert storage.bucket_size() == expected  # Pas de deuxième appel réseau.
        stub.assert_no_pending_responses()


def test_storage_types_are_summed_for_same_day_and_cache_expires(monkeypatch):
    storage = S3Storage('my-bucket', 'eu-west-3')
    client = storage.cloudwatch_client = Mock()

    def response(**kwargs):
        kind = kwargs['Dimensions'][1]['Value']
        if kind == 'StandardStorage':
            return {'Datapoints': [
                {'Timestamp': datetime(2026, 11, 1, tzinfo=timezone.utc), 'Average': 100},
                {'Timestamp': datetime(2026, 10, 31, tzinfo=timezone.utc), 'Average': 999},
            ]}
        if kind == 'GlacierStorage':
            return {'Datapoints': [
                {'Timestamp': datetime(2026, 11, 1, tzinfo=timezone.utc), 'Average': 25}]}
        return {'Datapoints': []}

    client.get_metric_statistics.side_effect = response
    assert storage.bucket_size() == {'size': 125, 'date': '2026-11-01'}
    assert client.get_metric_statistics.call_count == len(STORAGE_TYPES)
    storage._size_cached_at -= 3601
    storage.bucket_size()
    assert client.get_metric_statistics.call_count == 2 * len(STORAGE_TYPES)


def test_no_metric_is_not_reported_as_zero():
    storage = S3Storage('my-bucket', 'eu-west-3')
    storage.cloudwatch_client = Mock()
    storage.cloudwatch_client.get_metric_statistics.return_value = {'Datapoints': []}
    assert storage.bucket_size() == {'size': None, 'date': None}
    storage.cloudwatch_client.get_metric_statistics.reset_mock()
    storage.bucket_size()
    assert storage.cloudwatch_client.get_metric_statistics.call_count == len(STORAGE_TYPES)


def test_zero_size_is_a_valid_cached_measure():
    storage = S3Storage('my-bucket', 'eu-west-3')
    storage.cloudwatch_client = Mock()
    storage.cloudwatch_client.get_metric_statistics.return_value = {'Datapoints': [
        {'Timestamp': datetime(2026, 11, 1, tzinfo=timezone.utc), 'Average': 0}]}
    assert storage.bucket_size() == {'size': 0, 'date': '2026-11-01'}
    storage.cloudwatch_client.get_metric_statistics.reset_mock()
    assert storage.bucket_size() == {'size': 0, 'date': '2026-11-01'}
    storage.cloudwatch_client.get_metric_statistics.assert_not_called()


def test_cloudwatch_reuses_configured_session(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(boto3, 'Session', factory)
    storage = S3Storage('my-bucket', 'eu-west-3', 'profile')
    storage._get_client()
    storage._get_client('cloudwatch')
    factory.assert_called_once_with(profile_name='profile')
    assert factory.return_value.client.call_args_list == [
        (('s3',), {'region_name': 'eu-west-3'}),
        (('cloudwatch',), {'region_name': 'eu-west-3'})]


def test_size_endpoint_and_cloudwatch_failure_leave_navigation_available(tmp_path):
    storage = SimulatedStorage()
    app = create_app({'TESTING': True, 'DESTINATION': tmp_path}, storage)
    try:
        client = app.test_client()
        result = client.get('/api/bucket-size')
        assert result.status_code == 200
        assert result.json['size'] == sum(len(data) for data in storage.objects.values())
        storage.bucket_size = Mock(side_effect=ClientError(
            {'Error': {'Code': 'AccessDenied', 'Message': 'private details'}}, 'GetMetricStatistics'))
        result = client.get('/api/bucket-size')
        assert result.status_code == 502
        assert 'cloudwatch:GetMetricStatistics' in result.json['error']
        assert 'private details' not in result.json['error']
        assert client.get('/api/browse').status_code == 200
    finally:
        app.extensions['transfers'].shutdown()
