#!/bin/bash

hour=$(date +%H)
if [ "$hour" -lt 12 ]; then label="AM"
elif [ "$hour" -lt 17 ]; then label="MID"
else label="PM"
fi

timestamp=$(date +%Y-%m-%d)
filename="leadgen-ads/${timestamp}-${label}.md"

mkdir -p leadgen-ads

cat <<EOF2 > "$filename"
# Lead Gen Conquest Tracker – $timestamp ($label)

### Branded Terms Monitored
- Mike Hostilo Law Firm
- Hostilo Law
- Mike Hostilo attorney
- Hostilo accident lawyer
- Mike Hostilo + [city]

| Lead Gen Domain | Savannah | Macon | Augusta | Columbus | Notes |
|-----------------|----------|-------|---------|----------|-------|
| legalhelpnow.com |          |       |         |          |       |
| justiceclaimsupport.com |   |       |         |          |       |

EOF2

echo "Created $filename"
