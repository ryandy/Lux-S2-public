# Lux AI Season 2 - ry_andy_

This Python agent placed 1st out of 646 teams in [Lux AI Season 2](https://www.kaggle.com/competitions/lux-ai-season-2/overview).

The Lux AI Challenge is a competition where competitors design agents to tackle a multi-variable optimization, resource gathering, and allocation problem in a 1v1 scenario against other competitors. In addition to optimization, successful agents must be capable of analyzing their opponents and developing appropriate policies to get the upper hand.

You can read my post-competition writeup [here](https://www.kaggle.com/competitions/lux-ai-season-2/discussion/407982).

## To setup and run

- Clone this repo and the [v2.2.0 runner](https://github.com/Lux-AI-Challenge/Lux-Design-S2/tree/v2.2.0)
- Follow setup instructions described [here](https://github.com/Lux-AI-Challenge/Lux-Design-S2/blob/v2.2.0/README.md#getting-started)
- From the Lux-S2-public directory, run a test match `luxai-s2 ./main.py ./main.py -v 1 -o replay.html -s 0 -l 1000`
  - This may take up to an hour to run. For a quicker runtime, reduce [FUTURE_LEN](https://github.com/ryandy/Lux-S2-public/blob/main/luxry/util.py#L23) to 5.
- To view a replay of the match, open replay.html e.g. `file:///path/to/Lux-S2-public/replay.html` in a browser (you will have to modify the path in the URL).
