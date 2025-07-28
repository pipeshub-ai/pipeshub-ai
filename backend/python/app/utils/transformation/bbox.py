def transform_bbox_to_corners(bbox: dict) -> list[list[float]]:
    """
    Transform bounding box from {l, t, r, b} format to corner coordinates.

    Args:
        bbox (dict): Bounding box dictionary with keys 'l', 't', 'r', 'b', 'coord_origin'
                    - l: left coordinate
                    - t: top coordinate
                    - r: right coordinate
                    - b: bottom coordinate
                    - coord_origin: "BOTTOMLEFT" or "TOPLEFT"

    Returns:
        list: Four corner coordinates as [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
              Order: top-left, top-right, bottom-right, bottom-left
    """
    left, top, right, bottom = bbox['l'], bbox['t'], bbox['r'], bbox['b']
    coord_origin = bbox.get('coord_origin', 'TOPLEFT')

    if coord_origin == "BOTTOMLEFT":
        actual_top = max(top, bottom)
        actual_bottom = min(top, bottom)
    else:
        actual_top = min(top, bottom)
        actual_bottom = max(top, bottom)

    corners = [
        [left, actual_top],        # top-left
        [right, actual_top],        # top-right
        [right, actual_bottom],     # bottom-right
        [left, actual_bottom]      # bottom-left
    ]

    return corners

def normalize_corner_coordinates(corners: list[list[float]], page_width: float, page_height: float) -> list[list[float]]:
    """
    Normalize corner coordinates to [0, 1] range using page dimensions.

    Args:
        corners (list): Corner coordinates as [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
        page_width (float): Width of the page/canvas
        page_height (float): Height of the page/canvas

    Returns:
        list: Normalized corner coordinates where each coordinate is in [0, 1] range
    """
    normalized_corners = []

    for corner in corners:
        x, y = corner
        normalized_x = x / page_width
        normalized_y = y / page_height
        normalized_corners.append([normalized_x, normalized_y])

    return normalized_corners


def denormalize_corner_coordinates(normalized_corners: list[list[float]], page_width: float, page_height: float) -> list[list[float]]:
    """
    Convert normalized coordinates back to absolute pixel coordinates.

    Args:
        normalized_corners (list): Normalized corner coordinates in [0, 1] range
        page_width (float): Width of the page/canvas
        page_height (float): Height of the page/canvas

    Returns:
        list: Absolute corner coordinates in pixels
    """
    absolute_corners = []

    for corner in normalized_corners:
        norm_x, norm_y = corner
        absolute_x = norm_x * page_width
        absolute_y = norm_y * page_height
        absolute_corners.append([absolute_x, absolute_y])

    return absolute_corners
