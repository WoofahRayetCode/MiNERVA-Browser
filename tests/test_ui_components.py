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
        self.assertEqual(len(fb._wrap_pills), 3)  # All + 2 regions
        fb.update_idletasks()
        fb._reflow_region_pills()
        placed = [p for p in fb._wrap_pills if p.winfo_manager() == "place"]
        self.assertEqual(len(placed), 3)

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

    def test_all_region_pills_are_placed(self):
        search_var = tk.StringVar(value="")
        region_specs = [
            ("usa", "USA"),
            ("europe", "Europe"),
            ("japan", "Japan"),
            ("world", "World"),
            ("asia", "Asia"),
            ("korea", "Korea"),
            ("china", "China"),
            ("australia", "Australia"),
            ("canada", "Canada"),
            ("brazil", "Brazil"),
            ("france", "France"),
            ("germany", "Germany"),
            ("italy", "Italy"),
            ("spain", "Spain"),
            ("netherlands", "Netherlands"),
            ("sweden", "Sweden"),
            ("russia", "Russia"),
            ("taiwan", "Taiwan"),
            ("hong_kong", "Hong Kong"),
            ("other", "Other"),
        ]
        region_vars = {k: tk.BooleanVar(value=False) for k, _ in region_specs}
        fb = FilterBar(
            self.root,
            search_var=search_var,
            on_search_change=lambda: None,
            on_clear_search=lambda: None,
            region_specs=region_specs,
            region_vars=region_vars,
            tag_specs=[],
            tag_vars={},
            on_filter_change=lambda: None,
        )
        fb.pack(fill="x")
        fb.update_idletasks()
        fb._pill_wrap.configure(width=320)
        fb._reflow_region_pills()
        self.assertEqual(len(fb._pills), len(region_specs))
        self.assertEqual(len(fb._wrap_pills), len(region_specs) + 1)
        for child in fb._wrap_pills:
            self.assertEqual(child.winfo_manager(), "place")
            self.assertGreaterEqual(child.winfo_y(), 0)
        wrap_h = int(fb._pill_wrap.cget("height") or 0)
        last_y = max(child.winfo_y() + child.winfo_reqheight() for child in fb._wrap_pills)
        self.assertGreaterEqual(wrap_h, last_y)
        self.assertGreater(max(child.winfo_y() for child in fb._wrap_pills), 0)
        fb.destroy()


if __name__ == "__main__":
    unittest.main()
