# MahaJobSearchTool
Pulls open QA / SDET / test automation jobs from product companies and bank, financial and insurance capability centres' own public ATS feeds (no job boards),
for roles in Hyderabad, Bangalore or remote India. Run: python pull_jobs.py

Company list = data/companies.csv: product companies, plus the global capability centres (GCCs) of banks, financial firms and insurers in Hyderabad and Bangalore (no IT services, consulting or staffing firms).
Columns: Company, Portal status, Careers address, Layoffs 3yr, ATS, Slug, Category, India locations.
- ATS + Slug filled: the pull reads that public feed directly (Greenhouse, Lever, Ashby, SmartRecruiters, Workday).
- Otherwise the pull reads the careers page and uses the job system it points to, or a headless browser where robots.txt allows.
- Portal status: A = public feed known, C = careers page only, D = skipped (robots), E = no readable job list found yet.

Filters (pull_jobs.py): TITLE keeps QA / QE / SDET / test / quality roles; SKIP drops senior, lead, manager, staff and
hardware roles; india_target keeps Hyderabad, Bangalore, remote-India and India-wide postings.

Output: data/new_jobs.json (postings not seen before), data/open_matching.json, data/coverage.json.
The first run only records a baseline, so the next run reports what is new.
Schedule: .github/workflows/pull.yml runs on weekdays at 5:30 AM and 10:30 AM IST.
