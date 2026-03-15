# feishu-to-obsidian

[中文](README.md) | English

Bulk-migrate your Feishu Wiki knowledge base to Obsidian, preserving the full hierarchy, auto-localizing image attachments, and supporting incremental sync. Let Claude index your knowledge base directly inside Obsidian for an AI-powered reading experience.

## Why feishu-to-obsidian?

Many people have spent years accumulating a knowledge base in Feishu, but Feishu's AI capabilities are limited — true knowledge retrieval and synthesis remain out of reach.

**feishu-to-obsidian solves two things:**

**1. Let AI understand your Feishu knowledge base**

Plugins like [Claudian](https://github.com/YishenTu/claudian) in Obsidian allow Claude to index your local vault — asking questions, summarizing, and surfacing connections. But that only works if the content is local. This tool converts your entire Feishu Wiki into an Obsidian vault, instantly unlocking AI capabilities for your knowledge base.

**2. Continuous incremental sync — both sides stay in sync**

This is not a "one-time export". Keep writing in Feishu as usual. Run the script periodically (or set up a crontab) and new or modified documents will sync to Obsidian automatically — no manual maintenance required.

**Highlights:**

- Recursively exports an entire Wiki space, mirroring the Feishu hierarchy
- Automatically downloads and localizes images/attachments
- Incremental sync: skips documents that haven't changed
- Optional: converts Feishu internal links to Obsidian wikilinks
- Supports frontmatter (created time, modified time, original URL)

---

## Quick Start

**Prerequisites:**

- Python 3.11+
- A Feishu (Lark) account with access to the target Wiki space

**Installation:**

```bash
git clone https://github.com/horacehht/feishu-to-obsidian.git
cd feishu-to-obsidian
pip install -r requirements.txt
```

---

## Configuration

Before running, you need to set up a Feishu Open Platform app and fill in `config.yaml`.

### Step 1: Get `app_id` and `app_secret`

**1. Go to the Feishu Open Platform**

Visit `https://open.feishu.cn/app` and log in with your Feishu account.

**2. Create a custom app**

Click "Create Enterprise Self-Built App", fill in the details, then click "Create".

- App name (e.g. `feishu-to-obsidian`)
- App description (anything works)

![1773504318503](image/README/1773504318503.png)

![1773504476245](image/README/1773504476245.png)

**3. Copy App ID and App Secret**

Left menu → "Credentials & Basic Info" → you'll find:

- **App ID**: fill into `app_id` in config.yaml
- **App Secret**: click "View" then fill into `app_secret`

⚠️ Treat App Secret like a password — never commit it to Git or share it.

![1773504644807](image/README/1773504644807.png)

**4. Enable required permissions**

Left menu → "Permission Management" → search and enable the following three permissions:

| Permission ID              | Description                    |
| -------------------------- | ------------------------------ |
| `wiki:wiki:readonly`     | Read Wiki spaces and nodes     |
| `docx:document:readonly` | Read document content          |
| `drive:drive:readonly`   | Download images from documents |

![1773504833576](image/README/1773504833576.png)

![1773504990705](image/README/1773504990705.png)

**5. Add a redirect URL**

Left menu → "Security Settings" → add `http://localhost:9898/callback` under Redirect URLs.

![1773507434552](image/README/1773507434552.png)

**6. Publish the app**

Left menu → "Version Management & Release" → create a version → request release.

> Note: publishing within an organization requires admin approval; if you are the admin you can approve it directly.

![1773505047146](image/README/1773505047146.png)

![1773505125183](image/README/1773505125183.png)

### Step 2: Get `space_id` (Wiki Space ID)

1. Open the target Wiki space home page in Feishu and click "Wiki Settings".
2. The browser address bar will change to:
   `https://xxx.feishu.cn/wiki/settings/<space_id>`
3. Copy the ID at the end and fill it into `space_id` in config.yaml.

![1773506491621](image/README/1773506491621.png)

### Step 3: Fill in config.yaml

Full configuration example with field descriptions:

```yaml
# Feishu app credentials (from Feishu Open Platform)
# https://open.feishu.cn/app → create app → Credentials & Basic Info
app_id: "cli_xxxxxxxxxxxxxxxx"
app_secret: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# Output directory (your Obsidian vault path)
output_dir: "~/obsidian-vault"

# List of Wiki spaces to migrate (multiple supported)
wiki_spaces:
  - space_id: "xxxxxxxx"
    name: "knowledge"        # Local folder name for this space

# Optional: migrate a single document (document_id from the Feishu URL)
# single_docs:
#   - doc_id: "xxxxxxxx"

# Obsidian link style
# "wikilink" → [[filename]]
# "markdown" → [title](./path/file.md)
link_style: "wikilink"

# Attachment storage location — matches Obsidian's
# "Files and links → Default location for new attachments"
# vault_folder     → <output_dir>/<assets_dir>/
# same_folder      → same directory as the document
# subfolder        → <assets_dir>/ subfolder next to the document (default, recommended)
# specified_folder → same as vault_folder
attachments_location: "subfolder"

# Attachment subdirectory name
assets_dir: "attachments"

# Rate limit (Feishu API allows ~100 req/min)
rate_limit_per_minute: 60

# Incremental sync: tracks exported document modification times
sync_state_file: ".feishu_sync_state.json"

# Frontmatter options
frontmatter:
  include_created_time: true
  include_modified_time: true
  include_feishu_url: true
  include_owner: false
```

---

## Usage

### Migration (migrate.py)

```bash
python migrate.py                          # Full migration
python migrate.py --incremental            # Incremental sync (skip unchanged docs)
python migrate.py --limit 3               # Test: only migrate the first 3 documents
python migrate.py --doc-id <document_id>   # Debug: export a single document
python migrate.py --config other.yaml      # Use a different config file
```

On first run, **a browser window will open for OAuth authorization** — just click "Confirm" on the page. The token is cached in `.token_cache.json` and subsequent runs won't prompt again.

You can set up a crontab for scheduled incremental sync. Replace `/path/to/feishu-to-obsidian` with your actual project path:

```bash
# Every day at 7 AM
(crontab -l 2>/dev/null; echo "0 7 * * * cd /path/to/feishu-to-obsidian && python migrate.py --config config.yaml --incremental >> /tmp/feishu_sync.log 2>&1") | crontab -

# Every 2 hours
(crontab -l 2>/dev/null; echo "0 */2 * * * cd /path/to/feishu-to-obsidian && python migrate.py --config config.yaml --incremental >> /tmp/feishu_sync.log 2>&1") | crontab -

# Once a week, Monday at 9 AM
(crontab -l 2>/dev/null; echo "0 9 * * 1 cd /path/to/feishu-to-obsidian && python migrate.py --config config.yaml --incremental >> /tmp/feishu_sync.log 2>&1") | crontab -
```

Run `crontab -l` to view your current scheduled jobs. Sync logs are written to `/tmp/feishu_sync.log`.

### Post-process links (post_process.py, optional)

Converts raw Feishu links inside documents to Obsidian wikilinks:

```bash
python post_process.py           # Convert Feishu links to Obsidian wikilinks
python post_process.py --dry-run # Preview mode — no files are modified
```

### Estimate image storage before migration (count_images.py, optional)

```bash
python count_images.py           # Count images and estimate local storage usage
```

Run this script to get an estimate of how much disk space the images in your knowledge base will occupy before starting the full migration.

---

## Output

- Directory structure mirrors the Wiki hierarchy
- Image/attachment location is controlled by `attachments_location` (default: `subfolder` — stored in an `<assets_dir>/` subdirectory next to each document), aligned with Obsidian's four "Default location for new attachments" modes
- `.feishu_sync_state.json`: incremental sync state, stored in `~/obsidian-vault`
- `migrate_errors.json`: list of failed documents (created when errors occur)
- `post_process_report.json`: link-conversion summary report

---

## FAQ

**Q: Can't find the "Wiki Settings" entry?**
You need admin permission on the Wiki space to see the settings entry.

**Q: Getting a "permission denied" error at runtime?**
Check that you enabled all three permissions in Step 4 and that the app has been published (Version Management & Release).

**Q: A browser authorization page pops up on the first run?**
This is expected. After completing OAuth authorization once, the token is cached in `.token_cache.json` and the browser won't open again on subsequent runs.
