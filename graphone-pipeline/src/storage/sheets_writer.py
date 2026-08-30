"""
Writes pipeline output to the 6-tab Google Sheet deliverable:
Startups, Products, Research Papers, Jobs, News, Entity Mapping Log.

Uses `gspread` (service-account auth) since it's the simplest reliable path
for a batch writer that isn't part of a live web app. Batched via
`worksheet.update()` with a full 2D array rather than per-row `append_row`
calls — at 1,000+ rows per tab, per-row calls would burn through the Sheets
API's per-minute quota and take a very long time; a single batched write per
tab is orders of magnitude fewer API calls.

SETUP REQUIRED (see README): create a Google Cloud service account, enable the
Sheets API, download the JSON key to `credentials.json`, and share your target
spreadsheet with the service account's email address as an Editor. None of
that exists in this sandbox, so this module is not exercised live here — the
row-shaping functions below (`*_to_row`) ARE unit tested since they're pure
functions with no I/O.
"""

from __future__ import annotations

from typing import Any

from src.schemas import (
    EntityMappingLogRow, JobEntity, NewsEntity, ProductEntity, ResearchPaperEntity, StartupEntity,
)

TAB_STARTUPS = "Startups"
TAB_PRODUCTS = "Products"
TAB_PAPERS = "Research Papers"
TAB_JOBS = "Jobs"
TAB_NEWS = "News"
TAB_ENTITY_LOG = "Entity Mapping Log"

HEADERS: dict[str, list[str]] = {
    TAB_STARTUPS: ["schemaVersion", "recordType", "source.name", "source.url",
                   "content.entityName", "content.data.employeeCount", "collectedAt"],
    TAB_PRODUCTS: ["schemaVersion", "recordType", "source.name", "source.url",
                   "content.startupName", "content.productName", "content.pricingModel", "collectedAt"],
    TAB_PAPERS: ["schemaVersion", "recordType", "source.name", "source.url", "content.title",
                 "content.authors", "content.paper_url", "content.github_url",
                 "content.github_stars", "content.published_date", "collectedAt"],
    TAB_JOBS: ["schemaVersion", "recordType", "source.name", "source.url", "content.company",
               "content.rawCompany", "content.date", "content.is_remote", "content.role_family", "collectedAt"],
    TAB_NEWS: ["schemaVersion", "recordType", "source.name", "source.url", "content.headline",
               "content.published_date", "collectedAt"],
    TAB_ENTITY_LOG: ["raw_name", "canonical_name", "confidence", "method", "source_url"],
}


def startup_to_row(e: StartupEntity) -> list[Any]:
    return [e.schemaVersion, e.recordType.value, e.source.name, e.source.url,
            e.content.entityName, e.content.data.employeeCount, e.collectedAt]


def product_to_row(e: ProductEntity) -> list[Any]:
    return [e.schemaVersion, e.recordType.value, e.source.name, e.source.url,
            e.content.startupName, e.content.productName, e.content.pricingModel.value, e.collectedAt]


def paper_to_row(e: ResearchPaperEntity) -> list[Any]:
    return [e.schemaVersion, e.recordType.value, e.source.name, e.source.url, e.content.title,
            "; ".join(e.content.authors), e.content.paper_url, e.content.github_url,
            e.content.github_stars, e.content.published_date, e.collectedAt]


def job_to_row(e: JobEntity) -> list[Any]:
    return [e.schemaVersion, e.recordType.value, e.source.name, e.source.url, e.content.company,
            e.content.rawCompany, e.content.date, e.content.is_remote, e.content.role_family, e.collectedAt]


def news_to_row(e: NewsEntity) -> list[Any]:
    return [e.schemaVersion, e.recordType.value, e.source.name, e.source.url,
            e.content.headline, e.content.published_date, e.collectedAt]


def entity_log_to_row(r: EntityMappingLogRow) -> list[Any]:
    return [r.raw_name, r.canonical_name, r.confidence, r.method, r.source_url]


class SheetsWriter:
    def __init__(self, spreadsheet_id: str, credentials_path: str = "credentials.json"):
        self.spreadsheet_id = spreadsheet_id
        self.credentials_path = credentials_path
        self._client = None
        self._sheet = None

    def connect(self):
        import gspread
        self._client = gspread.service_account(filename=self.credentials_path)
        self._sheet = self._client.open_by_key(self.spreadsheet_id)

    def _get_or_create_tab(self, tab_name: str, n_cols: int):
        try:
            return self._sheet.worksheet(tab_name)
        except Exception:
            return self._sheet.add_worksheet(title=tab_name, rows=2000, cols=n_cols)

    def write_tab(self, tab_name: str, rows: list[list[Any]]):
        headers = HEADERS[tab_name]
        ws = self._get_or_create_tab(tab_name, len(headers))
        ws.clear()
        ws.update([headers] + rows, value_input_option="RAW")

    def write_all(
        self,
        startups: list[StartupEntity],
        products: list[ProductEntity],
        papers: list[ResearchPaperEntity],
        jobs: list[JobEntity],
        news: list[NewsEntity],
        entity_log: list[EntityMappingLogRow],
    ):
        self.write_tab(TAB_STARTUPS, [startup_to_row(e) for e in startups])
        self.write_tab(TAB_PRODUCTS, [product_to_row(e) for e in products])
        self.write_tab(TAB_PAPERS, [paper_to_row(e) for e in papers])
        self.write_tab(TAB_JOBS, [job_to_row(e) for e in jobs])
        self.write_tab(TAB_NEWS, [news_to_row(e) for e in news])
        self.write_tab(TAB_ENTITY_LOG, [entity_log_to_row(r) for r in entity_log])
