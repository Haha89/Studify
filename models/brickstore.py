from models.brick import Brick


class BrickStore:
    from models.colors import COLORS
    available_bricks = [Brick(i, c) for i, c in enumerate(COLORS.values())]

    @staticmethod
    def get_price_tile():
        """ Return the  price of one lego element in USD, according to Lego store"""
        return 0.03

    @staticmethod
    def get_price_base():
        """ Return the  price of one lego 32 * 32 base in USD, found on Amazon"""
        return 2
