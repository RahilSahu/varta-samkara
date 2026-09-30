# Adding a new blog post

To publish a new blog on the website, drop a Markdown file in this folder:

```
blogs/2026-10-05-my-blog-title.md
```

With this frontmatter at the top:

```markdown
---
title: My Blog Title
date: 2026-10-05
---

Your blog text here. Blank lines separate paragraphs.
Use ## for subheadings.
```

The daily 21:00 IST sync runs `generate_site.py` automatically and the new
post appears under the **Blog** section with an "Opinion" label and the
author byline. No other step is needed.

Rules: no em dashes in the text (use commas, colons, or hyphens).
