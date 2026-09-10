import tkinter as tk

from app import App


def test_gui_opens_and_checkboxes_respond():
    app = App()
    app.withdraw()
    app.update_idletasks()
    app.zhixian_var.set(True)
    app.zhicheng_var.set(False)
    app.photographer_changed()
    assert "植先" in app.status.get()
    app.zhixian_var.set(False)
    app.zhicheng_var.set(True)
    app.photographer_changed()
    assert "植丞" in app.status.get()
    app.destroy()
