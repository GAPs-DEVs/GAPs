"""악보 검색이 UI나 실행 중인 카메라에 의존하지 않는지 검사한다."""
from app.features.score_loader import list_available_scores


def test_score_list_combines_sample_examples_and_cache(tmp_path):
    examples = tmp_path / 'examples'
    cache = tmp_path / 'cache'
    examples.mkdir()
    cache.mkdir()
    sample = examples / 'score_practice_sample.json'
    for path in (sample, examples / 'song_parsed.json', cache / 'upload_parsed.json', cache / 'other.json'):
        path.write_text('[]', encoding='utf-8')
    (cache / 'directory_parsed.json').mkdir()
    assert list_available_scores(cache, sample) == [
        ('기본 연습 악보', str(sample)),
        ('song', str(examples / 'song_parsed.json')),
        ('upload', str(cache / 'upload_parsed.json')),
    ]


def test_sample_remains_available_without_user_cache(tmp_path):
    sample = tmp_path / 'score_practice_sample.json'
    sample.write_text('[]', encoding='utf-8')
    assert list_available_scores(tmp_path / 'missing-cache', sample) == [('기본 연습 악보', str(sample))]
