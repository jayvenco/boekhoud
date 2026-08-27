from urllib.parse import urlencode


def sort_url(request, current_sort: str, current_dir: str, col: str) -> str:
    """Bouwt de URL voor een sorteerbare kolomkop, met behoud van actieve filters."""
    params = dict(request.query_params)
    new_dir = "asc" if (current_sort == col and current_dir == "desc") else "desc"
    params["sort"] = col
    params["dir"] = new_dir
    return request.url.path + "?" + urlencode(params)
