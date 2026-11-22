# CodeJam2026
# Our Project
HackMatch is a Discord bot teammate matching platform designed specifically for hackathons. Instead of scrolling through dozens of #find-a-team posts and cold-DMing strangers, participants complete a short quiz and are matched with the most compatible teammates based on skills, goals, interests, and team preferences. 

## Inspiration
Finding teammates at hackathons is often stressful, inefficient, and time-consuming. Many participants spend valuable time browsing Discord channels, posting introductions, and messaging potential teammates without knowing whether they are still available or a good fit. The process can be especially daunting for more introverted participants who may be uncomfortable approaching strangers. We wanted to create a solution that makes team formation simple, compatible, and accessible for everyone.

## What it does
HackMatch is a teammate finder built in Discord, the communication platform used by most hackathons. Participants complete a short quiz containing the same information typically found in a #find-a-team post, such as technical skills, interests, goals, preferred team size, and level of competitiveness. Our matching algorithm then quantifies these responses and identifies the most compatible teammates based on complementary skills and shared objectives, helping users form stronger teams faster.

## How we built it
* Claude Sonnet 5: Leveraged agentic AI as a learning experience to power the solution.
* Python: Used for data processing, modelling, and developing the compatibility scoring algorithm.
* Discord Developer Portal: Created and configured a Discord bot for seamless integration into hackathon servers.

## Challenges we ran into
This was our first time building an application for Discord, so we faced a steep learning curve when working on Discord Developer Portal. Another challenge was designing a matching algorithm that could accurately represent human preferences and team compatibility using numerical scores. Balancing complementary skills with shared interests while keeping the system fair and intuitive required several iterations and adjustments.

## Accomplishments that we're proud of
We are proud of successfully creating a fully functional Discord bot that solves a real problem experienced at nearly every hackathon. We designed and implemented our own matching system from scratch and transformed an often chaotic team formation process into a simple and automated experience. Most importantly, we created a tool that could genuinely help participants spend less time searching for teammates and more time building great projects.

## What we learned
Throughout this project, we learned how Discord bots are built and deployed, how slash commands and user interactions work, and how recommendation systems can be implemented using scoring algorithms. We also gained experience rapidly developing a product under hackathon constraints and learned how important user experience is when designing tools intended for large communities of participants.

## What's next for HackMatch
HackMatch has shown strong potential as a solution to one of the most common challenges at hackathons: finding the right teammates. Our next step is to test the platform with larger communities and hackathons involving hundreds of participants to evaluate how well the matching algorithm performs at scale. By gathering real-world feedback, we hope to further improve match quality and the overall team formation experience. Looking ahead, we'd love to see HackMatch become a standard tool used by hackathon organizers to help participants connect efficiently. And who knows, maybe one day HackMatch will be used to help participants find their teams at CodeJam 17!!
