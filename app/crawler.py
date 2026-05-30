def search_reddit(keyword: str) -> list[dict]:
    """Return mock Reddit posts relevant to the given keyword."""
    return [
        {
            "title": "Inventory management is painful",
            "body": "We still use Excel spreadsheets",
        },
        {
            "title": "Warehouse tracking takes hours",
            "body": "Everything is manual",
        },
    ]
