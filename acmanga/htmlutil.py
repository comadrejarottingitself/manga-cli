from html.parser import HTMLParser


class _AnchorCollector(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.items = []
        self.current = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag == "a" and self.current is None:
            self.current = {
                "href": attrs.get("href") or "",
                "title": attrs.get("title") or "",
                "class": attrs.get("class") or "",
                "text_parts": [],
                "alt_parts": [],
            }
            self.depth = 1
            return
        if self.current is not None:
            if tag == "a":
                self.depth += 1
            if tag == "img" and attrs.get("alt"):
                self.current["alt_parts"].append(attrs.get("alt"))

    def handle_endtag(self, tag):
        if self.current is None or tag.lower() != "a":
            return
        self.depth -= 1
        if self.depth > 0:
            return
        item = self.current
        item["text"] = " ".join(" ".join(item.pop("text_parts", [])).split())
        item["alt"] = " ".join(" ".join(item.pop("alt_parts", [])).split())
        self.items.append(item)
        self.current = None
        self.depth = 0

    def handle_data(self, data):
        if self.current is not None and data:
            self.current["text_parts"].append(data)


class _ImageCollector(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.items = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            self.items.append(dict(attrs))


class _OptionCollector(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.items = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "option":
            data = dict(attrs)
            self.current = {"value": data.get("value") or "", "text_parts": []}

    def handle_endtag(self, tag):
        if tag.lower() == "option" and self.current is not None:
            item = self.current
            item["text"] = " ".join(" ".join(item.pop("text_parts", [])).split())
            self.items.append(item)
            self.current = None

    def handle_data(self, data):
        if self.current is not None and data:
            self.current["text_parts"].append(data)


class _TextCollector(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        if data:
            self.parts.append(data)


def anchors(html):
    parser = _AnchorCollector()
    parser.feed(html or "")
    parser.close()
    return parser.items


def images(html):
    parser = _ImageCollector()
    parser.feed(html or "")
    parser.close()
    return parser.items


def options(html):
    parser = _OptionCollector()
    parser.feed(html or "")
    parser.close()
    return parser.items


def text(fragment):
    parser = _TextCollector()
    parser.feed(fragment or "")
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def has_class(attrs, name):
    classes = (attrs or {}).get("class") or ""
    return name in classes.split()
