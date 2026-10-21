import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import os
import tempfile
import unittest

import shared_lists


class SharedListsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.book = shared_lists.SharedLists(lambda: os.path.join(folder.name, "lists.json"))

    def said(self, text, by=""):
        return shared_lists.handle(text, self.book, by)

    def test_add_tick_remove_read(self):
        self.assertEqual(self.said("add milk, eggs and bread to the shopping list"), "Added milk, eggs, bread to the shopping list.")
        self.assertEqual(self.said("Add milk to my grocery list"), "That's already on the shopping list.")
        self.assertEqual(self.said("check off eggs on the shopping list"), "Ticked off eggs on the shopping list.")
        self.assertEqual(self.said("what's on the shopping list?"), "Shopping (2): milk, bread.")
        self.assertEqual(self.said("remove bread from the shopping list"), "Took bread off the shopping list.")
        self.assertEqual(self.said("tick off bananas on the shopping list"), "Bananas isn't on the shopping list.")
        self.assertEqual(self.said("add call mom to my to do list", by="a guest"), "Added call mom to the to do list.")
        self.assertEqual(self.book.all()["To do"][0]["by"], "a guest")
        self.assertEqual(self.said("add tires to the garage list"), "Added tires to the garage list.")
        self.assertEqual(self.said("garage list"), "Garage (1): tires.")
        self.assertEqual(self.said("clear the shopping list"), "The shopping list is empty now.")
        self.assertEqual(self.said("shopping list"), "The shopping list is empty.")

    def test_other_things_are_left_alone(self):
        for text in ("read my email", "add a reminder", "check the oil", "add 5 and 7", "make a list of ideas for dinner"):
            self.assertIsNone(self.said(text), text)

    def test_clearing_only_the_ticked(self):
        self.book.add("Shopping", ["milk", "eggs"])
        self.book.change("Shopping", "eggs", done=True)
        self.assertEqual(self.book.clear("Shopping", done_only=True), 1)
        self.assertEqual([i["text"] for i in self.book.all()["Shopping"]], ["milk"])


if __name__ == "__main__":
    unittest.main()
