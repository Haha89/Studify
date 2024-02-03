import customtkinter


def run_gui():
    root = customtkinter.CTk()
    root.geometry("450x300")

    logo_label = customtkinter.CTkLabel(root, text="LegoMe",
                                        font=customtkinter.CTkFont(size=20, weight="bold"))
    logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

    label = customtkinter.CTkLabel(master=root, text="Size")
    label.grid(row=1, column=0, padx=0, pady=(20, 10))

    entry_size = customtkinter.CTkEntry(master=root, placeholder_text="1, 2, 3")
    entry_size.grid(row=1, column=1, padx=0, pady=(20, 10))

    label = customtkinter.CTkLabel(master=root, text="Path input")
    label.grid(row=2, column=0, padx=0, pady=(20, 10))

    entry_path = customtkinter.CTkEntry(master=root, placeholder_text="Path to image", width=250)
    entry_path.grid(row=2, column=1, columnspan=2, padx=20, pady=(20, 10))

    from legome.process import process
    button = customtkinter.CTkButton(master=root, text="Start",
                                     command=lambda: process(int(entry_size.get()), entry_path.get()))
    button.grid(row=3, column=1, padx=20, pady=(20, 10))
    root.mainloop()


if __name__ == "__main__":
    run_gui()
