from __future__ import annotations

import customtkinter

from studify.process import process

PAD_Y = (20, 10)


def run_gui() -> None:
    """Open a small window asking for a size and an image, then build the guide."""
    root = customtkinter.CTk()
    root.title("Studify")
    root.geometry("520x320")

    logo_label = customtkinter.CTkLabel(root, text="Studify", font=customtkinter.CTkFont(size=20, weight="bold"))
    logo_label.grid(row=0, column=0, columnspan=3, padx=20, pady=PAD_Y)

    customtkinter.CTkLabel(root, text="Size (baseplates)").grid(row=1, column=0, padx=20, pady=PAD_Y)
    entry_size = customtkinter.CTkEntry(root, placeholder_text="4")
    entry_size.grid(row=1, column=1, pady=PAD_Y)

    customtkinter.CTkLabel(root, text="Path input").grid(row=2, column=0, padx=20, pady=PAD_Y)
    entry_path = customtkinter.CTkEntry(root, placeholder_text="Path to image", width=250)
    entry_path.grid(row=2, column=1, columnspan=2, padx=20, pady=PAD_Y)

    status = customtkinter.CTkLabel(root, text="")
    status.grid(row=4, column=0, columnspan=3, padx=20, pady=PAD_Y)

    def on_start() -> None:
        status.configure(text="Building...")
        root.update_idletasks()
        try:
            # No matplotlib preview here: a second Tk window fights with this mainloop.
            canvas = process(int(entry_size.get()), entry_path.get(), plot=False)
        except (ValueError, FileNotFoundError) as exc:
            status.configure(text=f"Error: {exc}")
            return
        rows, cols = canvas.shape
        status.configure(text=f"Done: {cols} x {rows} studs, {canvas.get_price().format()}")

    customtkinter.CTkButton(root, text="Start", command=on_start).grid(row=3, column=1, padx=20, pady=PAD_Y)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
