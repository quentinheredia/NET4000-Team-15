# What is currently in 

- Overall brief network monitor (Currently shows switches and their links but not their hosts)
- Script to check switches, hosts, links and path taken from host 1 to host 2 


## Current big headache
- When trying to simulate links going down to see alternative path, it gets updated and reflects in the path trace script 
  - HOWEVER, when trying to bring said links back up everything seems to stop working 
  - Current suspicions:
    - L2 loop between all the switches trying to re-establish original flows even though they already exists.
