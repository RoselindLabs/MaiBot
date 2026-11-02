"""Actual GIF helper contracts using in-memory images; no model/QQ/cache access."""

import io

from PIL import Image
import pytest

from src.common.utils.utils_image import ImageUtils


BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)


def make_gif(colors):
    frames = [Image.new("RGB", (100, 100), color) for color in colors]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buffer.getvalue()


def assert_strip(output, colors):
    with Image.open(io.BytesIO(output)) as image:
        assert image.format == "JPEG"
        assert image.size == (200 * len(colors), 200)
        for index, color in enumerate(colors):
            actual = image.getpixel((200 * index + 100, 100))
            assert all(abs(channel - expected) <= 3 for channel, expected in zip(actual, color, strict=True))


@pytest.mark.parametrize("colors", [(BLACK, WHITE, RED), (WHITE, BLACK, RED)])
def test_default_threshold_keeps_distinct_frames_in_order(colors):
    assert_strip(ImageUtils.gif_2_static_image(make_gif(colors)), colors)


def test_similar_frames_compare_against_last_selected_frame():
    # MSE 400 is skipped, but MSE 1600 against the retained black frame is kept.
    colors = (BLACK, (20, 20, 20), (40, 40, 40))
    assert_strip(ImageUtils.gif_2_static_image(make_gif(colors)), (BLACK, colors[-1]))


def test_mse_equal_to_threshold_is_still_similar():
    assert_strip(ImageUtils.gif_2_static_image(make_gif((BLACK, WHITE)), similarity_threshold=65025), (BLACK,))


@pytest.mark.parametrize("max_frames", [1, 2, 3, 15])
def test_max_frames_caps_the_retained_strip(max_frames):
    colors = (BLACK, WHITE) * 9
    output = ImageUtils.gif_2_static_image(make_gif(colors), similarity_threshold=0, max_frames=max_frames)
    assert_strip(output, colors[:max_frames])


def test_default_max_frames_remains_fifteen():
    colors = (BLACK, WHITE) * 9
    assert_strip(ImageUtils.gif_2_static_image(make_gif(colors)), colors[:15])


@pytest.mark.parametrize("max_frames", [0, -1])
def test_nonpositive_max_frames_is_rejected(max_frames):
    with pytest.raises(ValueError, match="max_frames"):
        ImageUtils.gif_2_static_image(make_gif((BLACK, WHITE)), max_frames=max_frames)


def test_single_frame_gif_remains_valid():
    assert_strip(ImageUtils.gif_2_static_image(make_gif((RED,))), (RED,))
