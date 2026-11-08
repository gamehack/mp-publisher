from pathlib import Path


def test_reusable_overseas_cover_is_small_jpeg():
    cover = Path(__file__).resolve().parents[1] / "assets" / "overseas-stories-cover-v1.jpg"
    image = cover.read_bytes()
    assert image.startswith(b"\xff\xd8\xff")
    assert image.endswith(b"\xff\xd9")
    assert len(image) <= 64 * 1024
