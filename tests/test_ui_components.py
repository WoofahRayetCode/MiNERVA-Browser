import unittest
import tkinter as tk
from minerva.ui.theme import (
    BG,
    PANEL,
    PANEL_ALT,
    ACCENT,
    FG,
    FG_DIM,
    SEL_BG,
    ENTRY_BG,
    BORDER,
    SUCCESS,
    WARNING,
    DANGER,
    setup_modern_styles,
)
from minerva.ui.components.filter_bar import FilterBar, RegionPill
from minerva.ui.components.tools_dialog import ToolsMenu


class TestUIComponents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def test_theme_constants(self):
        self.assertTrue(BG.startswith("#"))
        self.assertTrue(PANEL.startswith("#"))
        self.assertTrue(ACCENT.startswith("#"))
        self.assertTrue(FG.startswith("#"))
        self.assertTrue(BORDER.startswith("#"))
        self.assertTrue(SUCCESS.startswith("#"))

    def test_setup_modern_styles(self):
        style = setup_modern_styles(self.root)
        self.assertIsNotNone(style)
        self.assertEqual(style.theme_use(), "clam")

    def test_region_pill_toggle(self):
        var = tk.BooleanVar(value=False)
        toggled = []
        pill = RegionPill(self.root, text="USA", var=var, command=lambda: toggled.append(True))
        self.assertFalse(var.get())

        pill._on_click()
        self.assertTrue(var.get())
        self.assertEqual(len(toggled), 1)

        pill._on_click()
        self.assertFalse(var.get())
        self.assertEqual(len(toggled), 2)
        pill.destroy()

    def test_filter_bar_creation_and_actions(self):
        search_var = tk.StringVar(value="")
        region_specs = [("usa", "USA"), ("europe", "Europe")]
        region_vars = {k: tk.BooleanVar(value=True) for k, _ in region_specs}
        tag_specs = [("demo", "Demo"), ("beta", "Beta")]
        tag_vars = {k: tk.BooleanVar(value=False) for k, _ in tag_specs}
        changes = []

        fb = FilterBar(
            self.root,
            search_var=search_var,
            on_search_change=lambda: changes.append("search"),
            on_clear_search=lambda: search_var.set(""),
            region_specs=region_specs,
            region_vars=region_vars,
            tag_specs=tag_specs,
            tag_vars=tag_vars,
            on_filter_change=lambda: changes.append("filter"),
        )

        self.assertIsNotNone(fb.search_entry)
        self.assertIsNotNone(fb.tags_btn)
        self.assertEqual(len(fb._pills), 2)

        # Test tag toggling
        fb._hide_all_tags()
        self.assertTrue(all(v.get() for v in tag_vars.values()))
        self.assertIn("🏷 Hide Tags (2) ▾", fb.tags_btn.cget("text"))

        fb._clear_tag_filters()
        self.assertFalse(any(v.get() for v in tag_vars.values()))
        self.assertEqual(fb.tags_btn.cget("text"), "🏷 Hide Tags ▾")

        # Test region toggle
        fb._on_all_regions_click()
        self.assertFalse(all(v.get() for v in region_vars.values()))

        fb.destroy()


if __name__ == "__main__":
    unittest.main()
