def process(size: int, path: str):
    from legome.models.canvas import Canvas
    from legome.models.pdf_creator import PdfCreator
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

    PdfCreator(canvas).generate_html()
