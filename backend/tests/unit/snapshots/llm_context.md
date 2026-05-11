# Available entities

## incident (Incident)
An IT support ticket reporting a problem or service disruption

Fields:
- number (Ticket Number, string): Human-friendly ticket identifier, e.g., INC0012345
- short_description (Summary, string): One-line summary of the issue
- description (Description, string): Full description of the issue from the reporter
- state (Status, integer with value map [1=New, 2=In Progress, 3=On Hold, 4=Resolved, 5=Closed]): Current state of the incident
- priority (Priority, integer with value map [1=Critical, 2=High, 3=Medium, 4=Low, 5=Planning]): Severity level
- category (Category, string): Functional category, e.g., Network, Software, Hardware
- caller_id (Reporter (sys_id), reference -> sys_user): Reference to the user who reported the incident
- assigned_to (Assignee (sys_id), reference -> sys_user): Reference to the user currently working on the incident
- assignment_group (Assigned Team (sys_id), reference -> sys_user_group): Reference to the team responsible for the incident
- sys_created_on (Created, datetime): Timestamp the incident was created
- sys_updated_on (Last Updated, datetime): Timestamp of the most recent change

Outbound relations:
- reportedBy: incident reported by sys_user (cardinality many_to_one)
- assignedTo: incident assigned to sys_user (cardinality many_to_one)
- handledBy: incident handled by team sys_user_group (cardinality many_to_one)

## sys_user (User)
An employee or external user of the IT system

Fields:
- email (Email, string): Corporate email address [SENSITIVE]
- name (Full Name, string)
- department (Department, string)
- location (Location, string)
- manager (Manager (sys_id), reference -> sys_user): Reference to this user's manager
- active (Active, boolean)

Outbound relations:
- incidentsReported: sys_user raised incidents incident (cardinality one_to_many)
- incidentsAssigned: sys_user is assigned incident (cardinality one_to_many)
- managedBy: sys_user managed by sys_user (cardinality many_to_one)
- manages: sys_user manages sys_user (cardinality one_to_many)

## sys_user_group (Assignment Group)
A team of IT staff that handles a category of incidents

Fields:
- name (Team Name, string)
- manager (Team Manager (sys_id), reference -> sys_user)
- category (Category Handled, string)

Outbound relations:
- incidentsHandled: sys_user_group handles incidents incident (cardinality one_to_many)
- managedBy: sys_user_group managed by sys_user (cardinality many_to_one)

## kb_knowledge (Knowledge Base Article)
A published help article describing how to resolve an issue

Fields:
- number (Article Number, string)
- short_description (Title, string)
- text (Body, string)
- kb_category (Category, string)
- workflow_state (Publication Status, string)
