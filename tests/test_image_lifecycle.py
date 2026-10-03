import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from drape.ui import images


class ImageLifecycleTest(unittest.TestCase):
    def test_queued_result_is_ignored_after_widget_destruction(self):
        image = SimpleNamespace(connect=mock.Mock(), set_from_icon_name=mock.Mock())
        done = mock.Mock()
        with (
            tempfile.TemporaryDirectory() as temp,
            mock.patch.object(images, "THUMB_DIR", Path(temp)),
            mock.patch.dict(images._pixbufs, {}, clear=True),
            mock.patch.object(images, "_frames", return_value=None),
            mock.patch.object(images, "_decode", return_value=mock.Mock()),
            mock.patch.object(images._images, "submit") as jobs,
            mock.patch.object(images.GLib, "idle_add") as dispatch,
            mock.patch.object(images, "_show") as show,
        ):
            path = Path(temp) / "preview.png"
            path.touch()
            images.load_image(str(path), image, 280, 171, on_done=done)
            jobs.call_args.args[0]()
            image._drape_destroyed = True
            callback, *args = dispatch.call_args.args
            callback(*args)
            show.assert_not_called()
            done.assert_not_called()
            image.set_from_icon_name.assert_not_called()

    def test_queued_error_is_ignored_after_widget_destruction(self):
        image = SimpleNamespace(connect=mock.Mock(), set_from_icon_name=mock.Mock())
        done = mock.Mock()
        with (
            tempfile.TemporaryDirectory() as temp,
            mock.patch.object(images, "THUMB_DIR", Path(temp)),
            mock.patch.object(images._images, "submit") as jobs,
            mock.patch.object(images.GLib, "idle_add") as dispatch,
            mock.patch.object(images, "_frames", side_effect=ValueError("bad image")),
        ):
            path = Path(temp) / "preview.png"
            path.touch()
            images.load_image(str(path), image, 280, 171, on_done=done)
            jobs.call_args.args[0]()
            image._drape_destroyed = True
            callback, *args = dispatch.call_args.args
            callback(*args)
            image.set_from_icon_name.assert_not_called()
            done.assert_not_called()
