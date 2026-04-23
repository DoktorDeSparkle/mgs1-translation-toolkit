# UI/UX Review: MGS Dialogue Editor

## Overall Impression

This is a specialized desktop tool for a niche workflow (injecting subtitles into PSX game data). The audience is small and technical, which forgives some rough edges -- but several issues would still slow down even expert users. The app has grown organically from a Radio-only editor into a four-mode tool, and some seams from that evolution are visible.

---

## 1. Hierarchy and Visual Structure

**Issue: The right panel lacks a clear primary action.**
The right-side column stacks: Preview, Dialogue editor, Timing fields, then a pile of buttons (Apply Edit, Split, Delete, Revert, Translate, Auto-format) plus Open Folder, Open Project, Finalize, Edit Prompts, Edit Save Locations, Edit Contact Names, and Quit. That's **12+ buttons** in a vertical column with no visual grouping. The "Apply Edit" button -- the most frequently used action -- has the same visual weight as "Quit."

**Recommendation:** Group the editing buttons (Apply, Split, Delete, Translate, Auto-format) into a labeled `QGroupBox` like "Edit Actions". Move the file operations (Open Folder, Open Project, Finalize) out of the button column entirely -- they're already in the File menu and add clutter. The "Quit" button in the main window body is redundant with Cmd+Q and the window close button.

---

## 2. Cognitive Load

**Issue: The Edit menu contains placeholder/vestigial items.**
The `.ui` file defines Edit menu items "Another option", "Copy", and "Pasta" (typo for Paste). These appear to be leftover scaffolding from the original Qt Designer template. They're never wired up in `mainwindow.py` and the Edit menu isn't rebuilt programmatically (unlike the File menu). A user who opens Edit will see broken, non-functional items alongside the real "Preferences" action.

**Recommendation:** Clear and rebuild the Edit menu the same way the File menu is rebuilt, or at minimum remove the placeholder actions from `form.ui`.

**Issue: Technical jargon in labels.**
Labels like "Call (Offset)", "Vox Block:", "Vox Address:", "Frequency:" with an LCD-style display are deeply domain-specific. This is partially unavoidable given the audience, but the LCD widget (`QLCDNumber` with green text) is a novelty that doesn't help readability and takes up horizontal space in the status bar.

**Recommendation:** Replace `QLCDNumber` with a plain `QLabel` styled with a monospace font. The retro aesthetic is charming but costs screen real estate and readability.

---

## 3. Feedback and System Status

**Positive:** The app handles this well in several areas:
- `closeEvent` warns about unsaved changes before quitting
- `closeProject` also checks for unsaved changes
- Status bar messages like "Changes applied (unsaved -- use File -> Save Project)" are specific and actionable
- The `FinalizeProgressDialog` shows real-time build output with a log capture -- excellent for a long-running operation
- Modified entries get a bullet marker in the offset list

**Issue: No feedback when data hasn't been loaded yet.**
When the app first launches, the offset list, audio cue list, subtitle list, and dialogue editor are all empty with no guidance. A new user sees a blank window and must figure out they need File -> Open Folder or Open Project.

**Recommendation:** Add placeholder text or a simple label in the offset list area: "Open a folder or project to begin" (empty state as onboarding).

---

## 4. Consistency and Standards

**Issue: The `.ui` file and runtime UI are significantly diverged.**
The `form.ui` defines a basic two-panel layout with a combo box, but `__init__` replaces the combo box with a custom `OffsetListWidget`, hides the original `subsPreviewList` and replaces it with `SubtitleTableWidget`, inserts numerous programmatic widgets, rebuilds the File menu from scratch, etc. The `.ui` file is essentially a skeleton that gets gutted on startup.

This isn't a user-facing issue, but it's a maintainability concern: Qt Designer can't be used to preview or edit the actual UI anymore. Any future contributor would need to trace through `__init__` to understand the layout.

**Issue: Mode-dependent button visibility is complex.**
Some buttons appear/disappear per mode (Split/Delete are radio-only, Revert is demo/vox/zmovie-only), and some checkboxes appear/disappear (Disc 1 Only, Unclaimed VOX, Skip VOX Sort). The `_hideRadioWidgets()` pattern works but makes it hard to predict what the UI looks like in each mode without running the app.

**Issue: `Ctrl+F` for Auto-format conflicts with the universal "Find" convention.**
macOS users expect Cmd+F to search/find. Using it for "auto-format text wrapping" violates Jakob's Law. Similarly, `Ctrl+P` was reassigned from Play to Open Project, which is fine (Cmd+P = Print is the convention), but the `.ui` still defines the old `Ctrl+P` shortcut on `playVoxButton`.

**Recommendation:** Move Auto-format to a different shortcut (e.g., `Ctrl+Shift+F` or `Ctrl+B` for "break lines"). The old shortcut in the `.ui` for playVoxButton should be removed since the code overrides it anyway.

---

## 5. Accessibility

**Issue: No keyboard shortcut for mode switching.**
The four modes (Radio/Demo/VOX/ZMovie) can only be switched via mouse click on the tab bar or the View menu. Power users working through hundreds of entries would benefit from `Ctrl+1/2/3/4` to switch modes directly.

**Issue: The `QLCDNumber` widget has no accessibility label.**
Screen readers can't meaningfully describe an LCD-style number display. A plain `QLabel` would be accessible by default.

**Positive:** Good use of `buddy` relationships in the `.ui` file (labels associated with their input widgets). Good use of `toolTip` on most buttons.

---

## 6. Forms and Input

**Positive:** The Preferences dialog is well-structured with grouped sections (Translation, Editor, Build, Subtitle Preview). The slider+spinbox sync for FPS tuning is a nice touch.

**Issue: `startFrameBox` has `displayIntegerBase` set to 9 in the `.ui` file.**
This is a bug that gets fixed at runtime (`setDisplayIntegerBase(10)`) but shouldn't exist in the `.ui`. Similarly, `durationBox` has no `maximum` set in the `.ui` (fixed to 99999999 at runtime).

**Issue: The Dialogue text editor (`QTextEdit`) doesn't have a character count or line-length indicator.**
Given that subtitle text has strict byte limits per the game format, a character/byte counter near the text editor would prevent overflow errors at compile time.

**Recommendation:** Add a `QLabel` below the `DialogueEditorBox` showing current character count vs. limit, updated on each keystroke.

---

## 7. Navigation and Wayfinding

**Positive:** Prev/Next buttons with Cmd+Up/Down shortcuts are good. The frequency filter and disc-1-only checkbox help narrow large offset lists.

**Issue: No search/filter for offset lists in Demo/VOX/ZMovie modes.**
Radio mode has frequency filtering, but Demo and VOX modes with potentially hundreds of entries have no search or filter mechanism.

**Recommendation:** Add a filter text field above the offset list that works across all modes (simple substring match on entry names).

---

## 8. Empty States and Edge Cases

**Issue: No first-run experience or onboarding.**
The app opens to a completely blank state. The buttons on the right (Open Folder, Open Project) help, but the main content area gives no indication of what the tool does or what to do first.

**Issue: Destructive actions need proportional confirmation.**
`deleteSubtitle` and `closeProject` should have confirmation dialogs (close project does; need to verify delete). The "Revert to Original" button has a preference-controlled warning (`editor/warn_on_revert`) which is good.

---

## 9. Trust and Transparency

**Positive:** The Finalize dialog with checkable groups and real-time log output builds trust during the critical "compile game data" step. The warning when unchecking STAGE.DIR is well-written and specific about consequences.

**Issue: The "Overwrite files in output folder on each build" checkbox is checked by default.** This should default to unchecked, since overwriting original game files is destructive and hard to reverse.

---

## 10. Quick Heuristic Checklist

| Area | Status |
|---|---|
| Hierarchy | Needs work -- button column is a flat list with no grouping |
| Feedback | Good -- status bar messages are specific and actionable |
| Errors | Good -- compile errors shown in progress dialog |
| Consistency | Mixed -- Edit menu has broken placeholders; `.ui` diverges from runtime |
| Accessibility | Needs work -- LCD widget, no mode-switch shortcuts |
| Cognitive load | High button count in right panel; technical jargon (expected for audience) |
| Empty states | Missing -- no first-run guidance |
| Trust | Good -- destructive ops mostly guarded with confirmation |

---

## Priority Fixes (highest impact, lowest effort)

1. **Clean up the Edit menu** -- remove "Another option", "Copy", "Pasta" from `form.ui` or rebuild the menu in code
2. **Remove the redundant Quit button** from the main window body
3. **Add an empty-state message** to the offset list area on launch
4. **Change Ctrl+F shortcut** for Auto-format to avoid conflicting with Find
5. **Fix the `.ui` bugs** -- base-9 display, missing duration maximum (even though they're patched at runtime, the `.ui` should be correct)
6. **Group the editing buttons** visually to reduce the flat button-list feel
7. **Default "Overwrite" to unchecked** in the Finalize dialog
