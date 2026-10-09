"""Network-free HTML paragraph extraction worker. Input is fetched by parent."""
import json
from html.parser import HTMLParser
import resource
import sys

# Prevent disk/output floods and runaway CPU in the offline worker.
resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_CPU, (20, 20))


class Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.capture = 0
        self.parts = []
        self.title = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "footer", "header"}:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "h1", "h2", "h3", "li"}:
            self.capture += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer", "header"}:
            self.skip = max(0, self.skip - 1)
        if tag == "title":
            self.in_title = False
        if tag in {"p", "h1", "h2", "h3", "li"}:
            self.capture = max(0, self.capture - 1)
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip and self.capture:
            self.parts.append(data)
        if self.in_title:
            self.title.append(data)


if __name__ == "__main__":
    parser = Paragraphs()
    parser.feed(sys.stdin.read(10 * 1024 * 1024))
    body = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
    print(json.dumps({"title": "".join(parser.title), "content": body, "extraction_method": "offline_html_paragraphs"}, ensure_ascii=False))
