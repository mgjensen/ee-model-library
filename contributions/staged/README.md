# Staged Contributions

This folder holds draft modules flagged by the EE Model Builder (Claude) for Martin review.

How it works: when an operator submits a model containing logic not yet in the module registry, Claude flags it and creates a draft file here. Martin reviews, approves, and moves it into modules/core/ with a registry entry.

File naming: use the proposed module ID as filename, e.g. TAX_RO_001.md

Review checklist before moving to modules/core/:
1. Confirm logic not already covered by an existing module
2. Verify assumptions are sourced
3. Write unit test
4. Add registry entry
5. Move to modules/core/ as .py module
