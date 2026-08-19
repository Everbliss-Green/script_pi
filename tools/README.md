# tools

## `md2pdf.py`

Renders `SOP.md` into the handout PDF. Run it after changing the SOP so the two
do not drift.

```bash
python3 -m venv .venv && ./.venv/bin/pip install reportlab
./.venv/bin/python tools/md2pdf.py SOP.md RFTag_Messaging_SOP.pdf
```

`reportlab` is needed only to build the PDF — it is not a dependency of the
scripts themselves.

It handles the Markdown this document actually uses: headings, fenced code
blocks, tables, blockquote callouts, lists and inline formatting. Terminal
blocks are set in Courier at 7.1 pt and soft-wrapped with a continuation indent,
so a 125-character line like the serial-failure error stays readable instead of
running off the page.
