# When to Use Execution Guard



Use Execution Guard when a side effect is \*\*unsafe to run twice\*\*.



## Good use cases



Execution Guard is a strong fit for:



\- payments

\- emails

\- trades

\- external API mutations

\- webhook handlers

\- ticketing actions

\- database writes with business impact

\- AI agent tool calls that change the outside world



## Ask this simple question



> If this runs twice, do I care?



If the answer is \*\*yes\*\*, Execution Guard probably belongs there.



## Strong signals you need it



You should strongly consider Execution Guard if your system has:



\- retries after timeouts

\- uncertain completion

\- background jobs

\- distributed workers

\- API calls that mutate state

\- external side effects

\- partial failure risk

\- local state that can drift from real-world state



## Cases where you may not need it



Execution Guard is usually \*\*not necessary\*\* for:



\- pure reads

\- analytics queries

\- calculations without side effects

\- idempotent cache refreshes

\- internal operations where duplicate execution is harmless



## Mental model



Execution Guard is not about making retries disappear.



It is about making retries \*\*safe\*\*.



The goal is not:



> never try again



The goal is:



> never execute the same irreversible side effect twice

