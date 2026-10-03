import unittest
from types import SimpleNamespace
from unittest import mock

from drape.ui import profile
from drape import pling


class ProfilesTest(unittest.TestCase):
    def test_profile_api_escapes_username(self):
        with mock.patch.object(pling, "_get", return_value={"data": [{"personid": "a/b"}]}) as get:
            self.assertEqual(pling.profile("a/b")["personid"], "a/b")
            get.assert_called_once_with("person/data/a%2Fb")

    def test_uploads_request_is_author_scoped_and_paginated(self):
        data = {
            "totalitems": 2,
            "data": [{"id": "1", "personid": "Author"}, {"id": "2", "personid": "other"}],
        }
        with mock.patch.object(pling, "_get", return_value=data) as get:
            items, total = pling.uploads("author", 3)
            self.assertEqual([i.id for i in items], ["1"])
            self.assertEqual(total, 2)
            self.assertEqual(get.call_args.args[1]["user"], "author")
            self.assertEqual(get.call_args.args[1]["page"], 3)

    def test_profile_callbacks_ignore_destroyed_modal(self):
        state = SimpleNamespace(closed=True)
        profile.ProfileView.loaded_profile(state, {})
        profile.ProfileView.got_uploads(state, ([], 0))
        profile.ProfileView.failed_uploads(state, Exception("offline"))
