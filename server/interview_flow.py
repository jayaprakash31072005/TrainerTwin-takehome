"""Interview flow for the TrainerTwin mock-interview bot (Pipecat Flows).

The code, not the LLM, owns the question order. Each question is a node.
The only way forward is the `move_on` function, so the model cannot skip
ahead or invent its own questions.
"""

from pipecat.flows import FlowManager, NodeConfig

QUESTIONS = [
    "Tell me about a project you're proud of.",
    "What was the hardest technical problem in it?",
    "How did you know it was working well?",
    "What would you do differently now?",
]

GREETING = (
    "Hi, I'm your AI interview trainer. "
    "We'll do a short practice interview with four questions, "
    "and I'll give you quick feedback at the end. "
    "Speak naturally, and take your time if you need a pause. "
    f"Let's start. {QUESTIONS[0]}"
)

# Persona and voice rules. Set once and kept across every node.
ROLE = (
    "You are an AI interview trainer running a short mock interview in a "
    "spoken conversation. Tone: warm, encouraging and professional, but "
    "gently strict about vague answers. Everything you say is spoken aloud, "
    "so use short plain sentences, no lists, no emojis, no markdown. "
    "Keep every reply to one or two sentences. Never invent your own "
    "interview questions and never ask more than one question per reply."
)


async def move_on(flow_manager: FlowManager):
    """Call this once the user has answered the current question and any follow-up."""
    flow_manager.state["answered"] = flow_manager.state.get("answered", 0) + 1
    next_index = flow_manager.state["q"] + 1
    flow_manager.state["q"] = next_index
    if next_index < len(QUESTIONS):
        return None, question_node(next_index)
    return None, feedback_node()


def question_node(index: int) -> NodeConfig:
    """Node for question `index` (0-based)."""
    question = QUESTIONS[index]

    if index == 0:
        # Greeting speaks question 1 directly through TTS (no LLM call, so it
        # starts fast). The text is added to the context, so the model knows
        # question 1 was already asked.
        return {
            "name": "question_1",
            "role_message": ROLE,
            "task_messages": [
                {
                    "role": "developer",
                    "content": _answer_rules(question, "You just asked this question."),
                }
            ],
            "functions": [move_on],
            "pre_actions": [{"type": "tts_say", "text": GREETING}],
            "respond_immediately": False,
        }

    return {
        "name": f"question_{index + 1}",
        "task_messages": [
            {
                "role": "developer",
                "content": (
                    "First give a very short, natural acknowledgement of what the "
                    f"user just said (one short sentence). Then ask exactly: \"{question}\"\n"
                    + _answer_rules(question, "")
                ),
            }
        ],
        "functions": [move_on],
    }


def _answer_rules(question: str, intro: str) -> str:
    return (
        f"{intro} Current interview question: \"{question}\"\n"
        "Rules for handling the user's replies to this question:\n"
        "1. After their FIRST answer, reply with exactly ONE short follow-up "
        "question about something specific they said. If the answer was vague, "
        "too short or off topic, give ONE gentle nudge asking for a concrete "
        "example instead. Do not call any function on this reply.\n"
        "2. After they reply to your follow-up or nudge, call the move_on "
        "function immediately. Do not ask anything more about this question.\n"
        "3. If the first answer was already detailed and complete, you may skip "
        "the follow-up and call move_on right away.\n"
        "4. Never ask a different interview question yourself."
    )


def feedback_node() -> NodeConfig:
    return {
        "name": "feedback",
        "task_messages": [
            {
                "role": "developer",
                "content": (
                    "The interview is over. Using only what the user actually said, "
                    "give spoken feedback in two or three short sentences: one "
                    "specific strength, one specific thing to improve, and a brief "
                    "encouraging closing line. Do not ask any question. Do not use "
                    "lists."
                ),
            }
        ],
        "functions": [],
        # Runs after the feedback has been spoken.
        "post_actions": [{"type": "end_conversation"}],
    }


def initial_node() -> NodeConfig:
    return question_node(0)


def init_state(flow_manager: FlowManager) -> None:
    flow_manager.state["q"] = 0
    flow_manager.state["answered"] = 0
