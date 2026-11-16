---
lane_control_template: 1
max_lines: 60
read_by: every agent
---
<!-- fixed -->
Project facts for agents run by lane-control. They add to your persona, your brief and AGENTS.md and never relax
them. Where this file and AGENTS.md disagree, AGENTS.md wins: say so in your report.
<!-- /fixed -->

# Project
<!-- slot what: required. Two or three sentences in plain words: what this repo is, who uses it, what would hurt if it broke. -->

## Layout
<!-- slot layout: required. At most 10 rows, `path | what lives there`. Only what an agent needs to find its way. -->

## Branches and issues
<!-- slot branches: required. Default branch, PR base if different, issue tracker, key prefix for lanes or "none yet". -->

## Checks
<!-- slot checks: required. Table `command | when | where it is written`. Only commands written down in the repo. -->

## Do not run
<!-- slot no-run: required. Commands that wipe, touch real hosts or accounts, or need something a lane lacks, and why. -->

## Heavy work
<!-- slot heavy: required. Builds or runs that need a wrapper, one at a time, or a lot of memory. -->

## Private data
<!-- slot private: required. Names of directories and files agents must not open or show to third-party lanes. Names only. -->

## Acts on the host
<!-- slot host: required. Routes, scripts or buttons that reboot, delete, or change network or services. Workers stub them. -->

## Exposure
<!-- slot exposure: required. The repo visibility you were given (public, private, internal or unknown), and what agents must keep out of commits because of it. -->

## Where decisions live
<!-- slot decisions: required. Files that record owner decisions, and which must not change without an issue saying so. -->
