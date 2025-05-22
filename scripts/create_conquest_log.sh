#!/bin/bash

hour=$(date +%H)
if [ "$hour" -lt 12 ]; then label="AM"
elif [ "$hour" -lt 17 ]; then label="MID"
else label="PM"
fi

timestamp=$(date +%Y-%m-%d)
filename="conquested-ads/${timestamp}-${label}.md"

mkdir -p conquested-ads

cat <<EOF > "$filename"
# Google Ad Conquest Monitoring – $timestamp ($label)

### Search Term: "Mike Hostilo Law Firm"
- Top Paid Results:
  1. [Competitor Name] – [Ad Copy Snippet]
  2. ...
- Notes:
  - [e.g., Morgan & Morgan using “local lawyer near me” keyword targeting]
EOF

echo "Created $filename"

