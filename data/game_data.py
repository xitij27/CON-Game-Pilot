from typing import Optional

type Country = dict[str, str | int]  # {name, doctrine, cities}

GAME_DATA: dict[str, dict] = {
    "WW3 4X": {
        "speeds": ["Normal", "Apocalypse"],
        "regions": {
            "America": [
                {"name": "Argentina",      "doctrine": "Western",  "cities": 6},
                {"name": "Bolivia",        "doctrine": "Eastern",  "cities": 6},
                {"name": "Brazil",         "doctrine": "European", "cities": 8},
                {"name": "Canada",         "doctrine": "European", "cities": 6},
                {"name": "Chile",          "doctrine": "European", "cities": 5},
                {"name": "Colombia",       "doctrine": "Western",  "cities": 6},
                {"name": "Cuba",           "doctrine": "Eastern",  "cities": 5},
                {"name": "Mexico",         "doctrine": "European", "cities": 5},
                {"name": "Peru",           "doctrine": "Western",  "cities": 6},
                {"name": "USA",            "doctrine": "Western",  "cities": 9},
            ],
            "Nordic Europe": [
                {"name": "Austria",        "doctrine": "European", "cities": 5},
                {"name": "Belarus",        "doctrine": "Eastern",  "cities": 6},
                {"name": "Finland",        "doctrine": "Western",  "cities": 5},
                {"name": "France",         "doctrine": "European", "cities": 7},
                {"name": "Germany",        "doctrine": "European", "cities": 7},
                {"name": "Sweden",         "doctrine": "European", "cities": 6},
                {"name": "Norway",         "doctrine": "Western",  "cities": 5},
                {"name": "Poland",         "doctrine": "European", "cities": 6},
                {"name": "Russia",         "doctrine": "Eastern",  "cities": 9},
                {"name": "Ukraine",        "doctrine": "Eastern",  "cities": 6},
                {"name": "United Kingdom", "doctrine": "European", "cities": 6},
            ],
            "Eastern Europe": [
                {"name": "Belarus",        "doctrine": "Eastern",  "cities": 6},
                {"name": "Finland",        "doctrine": "Western",  "cities": 5},
                {"name": "Germany",        "doctrine": "European", "cities": 7},
                {"name": "Greece",         "doctrine": "European", "cities": 5},
                {"name": "Poland",         "doctrine": "European", "cities": 6},
                {"name": "Romania",        "doctrine": "Eastern",  "cities": 6},
                {"name": "Russia",         "doctrine": "Eastern",  "cities": 9},
                {"name": "Serbia",         "doctrine": "Eastern",  "cities": 5},
                {"name": "Sweden",         "doctrine": "European", "cities": 6},
                {"name": "Turkey",         "doctrine": "Western",  "cities": 6},
            ],
            "Mediterranean": [
                {"name": "Algeria",        "doctrine": "Eastern",  "cities": 6},
                {"name": "Austria",        "doctrine": "European", "cities": 5},
                {"name": "Egypt",          "doctrine": "Western",  "cities": 6},
                {"name": "France",         "doctrine": "European", "cities": 7},
                {"name": "Greece",         "doctrine": "European", "cities": 5},
                {"name": "Italy",          "doctrine": "European", "cities": 6},
                {"name": "Libya",          "doctrine": "Eastern",  "cities": 5},
                {"name": "Morocco",        "doctrine": "Western",  "cities": 5},
                {"name": "Serbia",         "doctrine": "Eastern",  "cities": 5},
                {"name": "Spain",          "doctrine": "European", "cities": 6},
                {"name": "Turkey",         "doctrine": "Western",  "cities": 6},
            ],
            "Middle East": [
                {"name": "Afghanistan",    "doctrine": "Eastern",  "cities": 6},
                {"name": "Egypt",          "doctrine": "Western",  "cities": 6},
                {"name": "Ethiopia",       "doctrine": "Eastern",  "cities": 6},
                {"name": "Iran",           "doctrine": "Eastern",  "cities": 7},
                {"name": "Iraq",           "doctrine": "Western",  "cities": 6},
                {"name": "Israel",         "doctrine": "Western",  "cities": 5},
                {"name": "Kazakhstan",     "doctrine": "Eastern",  "cities": 6},
                {"name": "Pakistan",       "doctrine": "Western",  "cities": 6},
                {"name": "Saudi Arabia",   "doctrine": "Western",  "cities": 6},
                {"name": "Syria",          "doctrine": "Eastern",  "cities": 6},
                {"name": "Turkey",         "doctrine": "Western",  "cities": 6},
            ],
            "Northern Africa": [
                {"name": "Algeria",        "doctrine": "Eastern",  "cities": 6},
                {"name": "Cameroon",       "doctrine": "European", "cities": 5},
                {"name": "Chad",           "doctrine": "Eastern",  "cities": 6},
                {"name": "D.R. Congo",     "doctrine": "Eastern",  "cities": 5},
                {"name": "Egypt",          "doctrine": "Western",  "cities": 6},
                {"name": "Ethiopia",       "doctrine": "Eastern",  "cities": 6},
                {"name": "Kenya",          "doctrine": "Western",  "cities": 5},
                {"name": "Libya",          "doctrine": "Eastern",  "cities": 5},
                {"name": "Mali",           "doctrine": "European", "cities": 5},
                {"name": "Morocco",        "doctrine": "Western",  "cities": 5},
                {"name": "Nigeria",        "doctrine": "Eastern",  "cities": 5},
            ],
            "Southern Africa": [
                {"name": "Angola",         "doctrine": "Eastern",  "cities": 5},
                {"name": "Cameroon",       "doctrine": "European", "cities": 5},
                {"name": "Chad",           "doctrine": "Eastern",  "cities": 6},
                {"name": "D.R. Congo",     "doctrine": "Eastern",  "cities": 5},
                {"name": "Ethiopia",       "doctrine": "Eastern",  "cities": 6},
                {"name": "Kenya",          "doctrine": "Western",  "cities": 5},
                {"name": "Mali",           "doctrine": "European", "cities": 5},
                {"name": "Mozambique",     "doctrine": "Eastern",  "cities": 6},
                {"name": "Namibia",        "doctrine": "European", "cities": 5},
                {"name": "Nigeria",        "doctrine": "Eastern",  "cities": 5},
                {"name": "South Africa",   "doctrine": "European", "cities": 5},
            ],
            "Asia": [
                {"name": "Afghanistan",    "doctrine": "Eastern",  "cities": 6},
                {"name": "China",          "doctrine": "Eastern",  "cities": 9},
                {"name": "India",          "doctrine": "Eastern",  "cities": 8},
                {"name": "Japan",          "doctrine": "Western",  "cities": 6},
                {"name": "Kazakhstan",     "doctrine": "Eastern",  "cities": 6},
                {"name": "Mongolia",       "doctrine": "Eastern",  "cities": 5},
                {"name": "Myanmar",        "doctrine": "Eastern",  "cities": 6},
                {"name": "Pakistan",       "doctrine": "Western",  "cities": 6},
                {"name": "Russia",         "doctrine": "Eastern",  "cities": 9},
                {"name": "Thailand",       "doctrine": "Western",  "cities": 6},
                {"name": "Vietnam",        "doctrine": "Eastern",  "cities": 6},
            ],
            "Oceania": [
                {"name": "Australia",      "doctrine": "Western",  "cities": 6},
                {"name": "China",          "doctrine": "Eastern",  "cities": 9},
                {"name": "Indonesia",      "doctrine": "European", "cities": 6},
                {"name": "Japan",          "doctrine": "Western",  "cities": 6},
                {"name": "Myanmar",        "doctrine": "Eastern",  "cities": 6},
                {"name": "New Zealand",    "doctrine": "European", "cities": 5},
                {"name": "North Korea",    "doctrine": "Eastern",  "cities": 4},
                {"name": "South Korea",    "doctrine": "Western",  "cities": 4},
                {"name": "Philippines",    "doctrine": "Western",  "cities": 5},
                {"name": "Thailand",       "doctrine": "Western",  "cities": 6},
                {"name": "Vietnam",        "doctrine": "Eastern",  "cities": 6},
            ],
        },
    },
    # Placeholder entries — add region/country data when ready
    "WW3 1X":             {"speeds": ["Normal"],              "regions": {}},
    "Flashpoint":         {"speeds": ["Normal"],              "regions": {}},
    "Battleground USA":   {"speeds": ["Normal"],              "regions": {}},
    "Overkill":           {"speeds": ["Normal", "Apocalypse"],"regions": {}},
    "Rising Tides":       {"speeds": ["Normal"],              "regions": {}},
    "Civil War America":  {"speeds": ["Normal"],              "regions": {}},
    "Pacific Theater":    {"speeds": ["Normal"],              "regions": {}},
    "Rising Sun Apocalypse": {"speeds": ["Apocalypse"],       "regions": {}},
    "Nuclear Winter":     {"speeds": ["Normal", "Apocalypse"],"regions": {}},
    "Blood and Oil":      {"speeds": ["Normal"],              "regions": {}},
    "Middle East Conflict":{"speeds": ["Normal"],             "regions": {}},
}


def get_game_types() -> list[str]:
    return list(GAME_DATA.keys())


def get_speeds(game_type: str) -> list[str]:
    return GAME_DATA.get(game_type, {}).get("speeds", ["Normal"])


def get_regions(game_type: str) -> list[str]:
    return list(GAME_DATA.get(game_type, {}).get("regions", {}).keys())


def get_countries(game_type: str, region: str) -> list[Country]:
    return GAME_DATA.get(game_type, {}).get("regions", {}).get(region, [])


def get_all_countries(game_type: str) -> list[Country]:
    """All unique countries across every region (used for Spy role)."""
    regions = GAME_DATA.get(game_type, {}).get("regions", {})
    seen: set[str] = set()
    result: list[Country] = []
    for countries in regions.values():
        for c in countries:
            if c["name"] not in seen:
                seen.add(c["name"])
                result.append(c)
    return sorted(result, key=lambda c: c["name"])


def find_country(game_type: str, name: str) -> Optional[Country]:
    """Case-insensitive country lookup across all regions."""
    name_lower = name.strip().lower()
    for c in get_all_countries(game_type):
        if c["name"].lower() == name_lower:
            return c
    return None


def find_country_in_region(game_type: str, region: str, name: str) -> Optional[Country]:
    name_lower = name.strip().lower()
    for c in get_countries(game_type, region):
        if c["name"].lower() == name_lower:
            return c
    return None
