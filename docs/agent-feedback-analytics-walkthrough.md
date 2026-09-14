# Agent Feedback & Analytics — Stakeholder Guide

How users rate the AI assistant, and how to read every number on the Assistant Analytics dashboard. *(Written for business readers — no technical background needed.)*

## 1 · How Users Give Feedback

1. **After a help answer** — a row of 5 stars appears under every reply. One click submits; a comment box then opens if the user wants to say more.
2. **After creating a service request** — the user is asked once, next to the **View Service Request** card. The whole flow is rated, not each question the agent asked along the way.
3. **Comments are optional.** Users rating 3 stars or below are gently prompted with *"What was missing?"*
4. Ratings are always tied to the signed-in user, so we know who said what and can follow up.

## 2 · Where to See It

Open **Dashboard → Assistant Analytics**. The page has two tabs:

- **Overview** — how the assistant is serving tenants. For business and operations.
- **Agent health** — how the assistant is performing technically. For the engineering team.

## 3 · Overview Tab — What Each Number Means

**Performance cards**

| Card | What it tells you |
|---|---|
| **Response Accuracy** | Of the questions users asked, the share the assistant actually answered. |
| **Questions Asked** | How many real questions users typed. Background system checks are excluded. |
| **Peak Concurrent Users** | The busiest hour's number of active conversations. |
| **Avg Response Time** | How long the assistant takes to reply, in seconds. |
| **Escalation Rate** | The share of questions the assistant could not answer — the ones a person has to pick up. |
| **Total Sessions** | How many separate conversations took place. |

**Summary strip**

| Figure | What it tells you |
|---|---|
| **Questions Not Answered** | The percentage the assistant failed to answer. The mirror of accuracy. |
| **User Satisfaction (CSAT)** | The average star rating, shown as a percentage of 5 stars. |
| **Avg Session Length** | How long a typical conversation lasts, in minutes. |
| **Fallback Rate** | How often the assistant replied "could you rephrase that?" instead of answering. |
| **Questions per Session** | How many questions a user asks in one sitting. |

**Cards below**

| Card | What it tells you |
|---|---|
| **Sessions by Hour** | When during the day people use the assistant. Useful for staffing and release timing. |
| **User Ratings** | The average score, how many people rated, and the spread from 1 to 5 stars. The spread matters: a 3.5 built from all 3s is a very different problem from one built from 5s and 1s. |
| **Top Users** | Who uses the assistant most, and the rating each of them gives. A heavy user with a low score is the most important row on the page. |
| **Top Questions** | What people ask most. A red **"N unanswered"** badge marks questions the assistant repeatedly fails — the first place to improve the help content. |
| **Recent Conversations** | The latest conversations, with the opening question, any comment left, the agent that handled it, and the rating. |

## 4 · Reading an Actual Conversation

1. Click any **session ID** in the conversations list.
2. You see the **full exchange** — every question, the reply it received, and the rating and comment on each one.
3. Ratings sit next to the answer that earned them, so a one-star with *"no idea"* can be traced to exactly what the assistant said.

## 5 · Agent Health Tab — What Each Number Means

| Section | What it tells you |
|---|---|
| **Slowest Step** | The part of answering that takes longest. Where to look if replies feel slow. |
| **Least Reliable API** | The platform service that fails most often when the assistant calls it. |
| **Biggest Token Consumer** | Which part of the assistant uses the most AI processing — the main driver of running cost. |
| **Where the Time Goes** | Every step ranked slowest first, so delays are visible at a glance. |
| **Platform API Reliability** | Success rate for each platform service the assistant depends on. When a request stalls, the cause is usually here rather than the AI. |
| **Token Spend by Prompt** | Which parts of the assistant consume the most processing, and how much. |
| **Where Service Requests Stall** | How many conversations reach each stage of creating a request — showing exactly where people drop off. |

## 6 · How Response Accuracy Is Worked Out

Of the questions users asked, the share the assistant answered.

> **Example** — 100 conversation turns. 30 are background system checks, leaving **70 real questions**. The assistant answered 63 of them.
> **Accuracy = 63 ÷ 70 = 90%**

Two supporting figures always travel with it:

- **Sample size** — how many questions the percentage covers.
- **Coverage** — that sample as a share of all questions asked.

This matters: *100% accuracy* means very little if it is measured over two questions out of eighty. Always read the percentage together with its coverage.

---

*The assistant now tells us how it is doing in users' own words — and every score links back to the answer that earned it.*
