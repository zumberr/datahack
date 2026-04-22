import json
import os
import re
import unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

try:
    import soupsieve as sv
except ImportError:
    sv = None

MAPPINGS_PATH = "data/mappings.json"
DEFAULT_OUTPUT_DIR = "data/processed"


def fetch_soup(url):
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def text_or_none(node):
    if not node:
        return None
    return node.get_text(" ", strip=True)


def normalize_url(base_url, link):
    if not link:
        return None

    absolute = urljoin(base_url, link)
    parts = urlsplit(absolute)
    cleaned_path = re.sub(r"/{2,}", "/", parts.path)
    return urlunsplit((parts.scheme, parts.netloc, cleaned_path, parts.query, parts.fragment))


def normalize_text(value):
    if value is None:
        return ""

    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.lower().strip()


def extract_section_text(soup, heading_text, content_selector):
    heading_text = normalize_text(heading_text)
    if not heading_text or not content_selector:
        return None

    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if heading_text in normalize_text(tag.get_text(" ", strip=True)):
            if sv:
                for node in tag.find_all_next():
                    if sv.match(content_selector, node):
                        return text_or_none(node)
            elif content_selector.startswith(".") and " " not in content_selector:
                content = tag.find_next(class_=content_selector[1:])
                return text_or_none(content)

            content = tag.find_next()
            return text_or_none(content)

    return None


def extract_labeled_values(soup, labels, heading_selector, value_selector):
    if not labels:
        return {}

    normalized_targets = []
    for entry in labels:
        label_text = normalize_text(entry.get("label"))
        if not label_text:
            continue
        normalized_targets.append((label_text, entry.get("field")))

    results = {}
    if heading_selector:
        heading_nodes = soup.select(heading_selector)
    else:
        heading_nodes = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])

    for heading in heading_nodes:
        label_key = normalize_text(heading.get_text(" ", strip=True))
        field_name = None
        for target_label, target_field in normalized_targets:
            if target_field in results:
                continue
            if label_key == target_label or target_label in label_key or label_key in target_label:
                field_name = target_field
                break
        if not field_name:
            continue

        value_node = None
        if value_selector:
            if sv:
                for node in heading.find_all_next():
                    if sv.match(value_selector, node):
                        value_node = node
                        break
            elif value_selector.startswith(".") and " " not in value_selector:
                value_node = heading.find_next(class_=value_selector[1:])
            else:
                value_node = heading.find_next(value_selector)
        else:
            value_node = heading.find_next()

        results[field_name] = text_or_none(value_node)

    return results


def parse_table(table):
    if not table:
        return None

    headers = [text_or_none(th) for th in table.select("thead th")] or None
    rows = []
    for row in table.select("tbody tr"):
        cells = [text_or_none(td) for td in row.select("td")]
        if headers and len(headers) == len(cells):
            rows.append(dict(zip(headers, cells)))
        else:
            rows.append(cells)

    return rows or None


def scrape_accordion(soup, source):
    selectors = source.get("selectors", {})
    item_selector = selectors.get("item")
    question_selector = selectors.get("question")
    answer_selector = selectors.get("answer")

    if not item_selector or not question_selector or not answer_selector:
        raise ValueError("accordion selectors missing: item/question/answer")

    results = []
    for item in soup.select(item_selector):
        question = text_or_none(item.select_one(question_selector))
        answer = text_or_none(item.select_one(answer_selector))
        if not question and not answer:
            continue
        results.append(
            {
                "question": question,
                "answer": answer,
                "item_type": source.get("item_type"),
                "category": source.get("category"),
                "source_url": source.get("url"),
            }
        )

    return results


def scrape_listing(soup, source):
    selectors = source.get("selectors", {})
    item_selector = selectors.get("item")
    title_selector = selectors.get("title")
    link_selector = selectors.get("link")
    link_attr = selectors.get("link_attr", "href")
    summary_selector = selectors.get("summary")
    extra_fields = source.get("extra_fields", {})

    if not item_selector or not title_selector:
        raise ValueError("listing selectors missing: item/title")

    results = []
    for item in soup.select(item_selector):
        title = text_or_none(item.select_one(title_selector))
        summary = text_or_none(item.select_one(summary_selector)) if summary_selector else None
        link = None
        if link_selector:
            link_node = item.select_one(link_selector)
            if link_node and link_node.has_attr(link_attr):
                link = normalize_url(source.get("url"), link_node.get(link_attr))

        if not title and not summary and not link:
            continue

        record = {
            "title": title,
            "summary": summary,
            "link": link,
            "item_type": source.get("item_type"),
            "category": source.get("category"),
            "source_url": source.get("url"),
        }
        for field_name, selector in extra_fields.items():
            record[field_name] = text_or_none(item.select_one(selector))

        enrich_with_detail(record, source)

        results.append(record)

    return results


def scrape_detail(soup, source):
    fields = source.get("fields", {})
    if not fields:
        raise ValueError("detail fields missing")

    record = {
        "item_type": source.get("item_type"),
        "category": source.get("category"),
        "source_url": source.get("url"),
    }
    for field_name, selector in fields.items():
        record[field_name] = text_or_none(soup.select_one(selector))

    return [record]


def scrape_grouped_listing(soup, source):
    selectors = source.get("selectors", {})
    grouping = source.get("grouping", {})
    item_selector = selectors.get("item")
    title_selector = selectors.get("title")
    heading_tag = grouping.get("heading_tag")
    heading_class = grouping.get("heading_class")
    extra_fields = source.get("extra_fields", {})

    if not item_selector or not title_selector:
        raise ValueError("grouped_listing selectors missing: item/title")
    if not heading_tag:
        raise ValueError("grouped_listing grouping missing: heading_tag")

    results = []
    for item in soup.select(item_selector):
        if heading_class:
            heading_node = item.find_previous(heading_tag, class_=heading_class)
        else:
            heading_node = item.find_previous(heading_tag)

        faculty = text_or_none(heading_node)
        record = {
            "title": text_or_none(item.select_one(title_selector)),
            "summary": text_or_none(item.select_one(selectors.get("summary")))
            if selectors.get("summary")
            else None,
            "link": None,
            "item_type": source.get("item_type"),
            "category": source.get("category"),
            "source_url": source.get("url"),
            "faculty": faculty,
        }

        link_selector = selectors.get("link")
        link_attr = selectors.get("link_attr", "href")
        if link_selector:
            link_node = item.select_one(link_selector)
            if link_node and link_node.has_attr(link_attr):
                record["link"] = normalize_url(source.get("url"), link_node.get(link_attr))

        for field_name, selector in extra_fields.items():
            record[field_name] = text_or_none(item.select_one(selector))

        enrich_with_detail(record, source)

        if record["title"] or record["summary"] or record["link"]:
            results.append(record)

    return results


def scrape_source(source):
    url = source.get("url")
    if not url:
        raise ValueError("source url missing")

    source_type = source.get("type")
    soup = fetch_soup(url)

    if source_type == "accordion":
        return scrape_accordion(soup, source)
    if source_type == "listing":
        return scrape_listing(soup, source)
    if source_type == "detail":
        return scrape_detail(soup, source)
    if source_type == "grouped_listing":
        return scrape_grouped_listing(soup, source)

    raise ValueError(f"unsupported source type: {source_type}")


def enrich_with_detail(record, source):
    detail = source.get("detail")
    if not detail or not detail.get("enabled"):
        return

    link = record.get("link")
    if not link:
        return

    try:
        soup = fetch_soup(link)
    except Exception:
        return

    presentation = extract_section_text(
        soup,
        detail.get("presentation_heading"),
        detail.get("presentation_selector"),
    )
    if presentation:
        record["presentation"] = presentation

    sections = detail.get("sections", {})
    for field_name, section in sections.items():
        section_text = extract_section_text(
            soup,
            section.get("heading"),
            section.get("selector"),
        )
        if section_text:
            record[field_name] = section_text

    label_sections = detail.get("label_sections", [])
    if label_sections:
        labeled_values = extract_labeled_values(
            soup,
            label_sections,
            detail.get("label_heading_selector"),
            detail.get("label_value_selector"),
        )
        record.update({k: v for k, v in labeled_values.items() if v})

    table_selector = detail.get("table_selector")
    if table_selector:
        record["price_table"] = parse_table(soup.select_one(table_selector))

    direct_fields = detail.get("direct_fields", {})
    for field_name, selector in direct_fields.items():
        value = text_or_none(soup.select_one(selector))
        if value:
            record[field_name] = value


def load_mappings(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_results(output_dir, source_id, results):
    os.makedirs(output_dir, exist_ok=True)
    file_name = f"{source_id}.json"
    file_path = os.path.join(output_dir, file_name)
    with open(file_path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)

    return file_path


def merge_outputs(output_dir, merge_config, results_by_source):
    output_name = merge_config.get("output")
    source_ids = merge_config.get("sources", [])
    grouped = merge_config.get("grouped")
    if not output_name or not source_ids:
        return None

    if grouped:
        merged = {target: [] for target in grouped.values()}
        for source_id in source_ids:
            for item in results_by_source.get(source_id, []):
                item_type = item.get("item_type")
                target = grouped.get(item_type)
                if target:
                    merged[target].append(item)
                else:
                    merged.setdefault("otros", []).append(item)
    else:
        merged = []
        for source_id in source_ids:
            merged.extend(results_by_source.get(source_id, []))

    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, output_name)
    with open(file_path, "w", encoding="utf-8") as handle:
        json.dump(merged, handle, ensure_ascii=False, indent=2)

    return file_path


if __name__ == "__main__":
    mappings = load_mappings(MAPPINGS_PATH)
    output_dir = mappings.get("output_dir", DEFAULT_OUTPUT_DIR)
    sources = mappings.get("sources", [])
    merge_configs = mappings.get("merge_outputs", [])

    if not sources:
        raise SystemExit("No sources configured in mappings.json")

    results_by_source = {}

    for source in sources:
        source_id = source.get("id", "source")
        category = source.get("category", "unknown")
        url = source.get("url", "")
        print(f"Scraping {category} ({source_id}) from {url}...")
        try:
            results = scrape_source(source)
            results_by_source[source_id] = results
            if source.get("save", True):
                file_path = save_results(output_dir, source_id, results)
                print(f"Saved {len(results)} items to {file_path}")
        except Exception as exc:
            print(f"Error scraping {source_id}: {exc}")

    for merge_config in merge_configs:
        merged_path = merge_outputs(output_dir, merge_config, results_by_source)
        if merged_path:
            print(f"Merged sources into {merged_path}")