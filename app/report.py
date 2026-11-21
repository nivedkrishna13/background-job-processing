def build_report(payload):
    title = payload["title"]
    items = payload["items"]

    return {
        "title": title,
        "item_count": len(items),
        "items": items,
    }
