# Mike Hostilo Law Firm Content Monitoring

This repository tracks branded keyword conquesting and lead generation ad behavior.

## Structure
- `conquested-ads/`: Hourly logs for branded search activity
- `leadgen-ads/`: Hourly logs for non-lawyer advertisers and lead gen platforms
- `logs/history/`: Archived anomalies and weekly summaries
- `templates/`: Reusable markdown templates for content ideas and ad monitoring
- `scripts/`: Future-ready tools for CSV exports, screenshots, dashboard generation

## Filevine backup
`scripts/filevine_backup.py` exports Filevine configuration, case lists and marketing attribution straight from the Filevine API with a Personal Access Token. Usage and setup are in the file header. Output lands in `filevine_backup/`, which is git-ignored because it can contain client data.
