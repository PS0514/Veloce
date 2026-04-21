# Logic for Person A to hand over to Person C (The n8n logic)

def detect_clash(new_start, new_end, existing_events):
    """
    Logic: 
    1. Loop through user's Google Calendar events.
    2. If (New_Start < Existing_End) AND (New_End > Existing_Start):
       RETURN CLASH_DETECTED
    """
    for event in existing_events:
        if new_start < event['end'] and new_end > event['start']:
            return {
                "clash": True,
                "conflicting_with": event['summary'],
                "suggestion": "Move to 1 hour later?"
            }
    return {"clash": False}