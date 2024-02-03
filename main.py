def main(size: int, path: str):
    from models.canvas import Canvas
    from models.pdf_creator import PdfCreator
    # Create a canvas and process an image
    canvas = Canvas(size)  # Nb of base tile for the biggest length
    canvas.process_image(path)
    # Plot the resulting canvas
    canvas.plot_canvas()
    # Print the distribution of bricks on the canvas
    from pprint import pprint

    distribution = canvas.get_distribution()
    pprint(distribution)
    pprint(f"Total elements: {sum(distribution.values())}")
    pprint(f"Total estimated cost: {canvas.get_cost()} USD")

    pprint(PdfCreator(canvas).generate_html())


if __name__ == "__main__":
    main(10, "./pics/dog.png")
