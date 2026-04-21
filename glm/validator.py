import json
from datetime import datetime

def validate_glm_output(output_str):
    try:
        data = json.loads(output_str)
        # Check against schema.json requirements
        if "entities" not in data:
            return False, "Missing 'entities' key"
        
        for entity in data["entities"]:
            required = ["type", "title", "confidence_score", "needs_clarification"]
            if not all(k in entity for k in required):
                return False, f"Entity missing fields: {entity.get('title', 'Unknown')}"
        
        return True, "Valid JSON"
    except json.JSONDecodeError:
        return False, "Invalid JSON format"

# Example Local Test
sample_input = "Meeting with Tony at 3pm tomorrow"
current_time = datetime.now().isoformat()
print(f"Testing logic for: {sample_input} at {current_time}")