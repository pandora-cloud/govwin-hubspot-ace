// Closed-enum sets that the AWS Partner Central CreateOpportunity API
// accepts. Mirrors src/ace/mapper.py constants on the Python side so
// the form's client-side validation matches what the submit_form_to_ace
// Lambda re-validates server-side. Any change here must also land in
// src/ace/mapper.py; the test_ace_enums.py drift test catches Python
// drift against the boto3 model.

export const PARTNER_NEED_OPTIONS: Array<{ label: string; value: string }> = [
  { label: "Architectural Validation", value: "Architectural Validation" },
  { label: "Business Presentation", value: "Business Presentation" },
  { label: "Competitive Intelligence", value: "Competitive Intelligence" },
  { label: "Pricing Assistance", value: "Pricing Assistance" },
  { label: "Technical Consultation", value: "Technical Consultation" },
  {
    label: "Total Cost of Ownership Evaluation",
    value: "Total Cost of Ownership Evaluation",
  },
  { label: "Deal Support", value: "Deal Support" },
  { label: "Support for Public Tender", value: "Support for Public Tender" },
];

export const DELIVERY_MODELS: string[] = [
  "SaaS or PaaS",
  "BYOL or AMI",
  "Managed Services",
  "Professional Services",
  "Resell",
  "Other",
];

export const OPPORTUNITY_TYPES: string[] = [
  "Net New Business",
  "Flat Renewal",
  "Expansion",
];

export const SALES_ACTIVITIES: string[] = [
  "Initialized discussions with customer",
  "Customer has shown interest in solution",
  "Conducted POC / Demo",
  "In evaluation / planning stage",
  "Agreed on solution to Business Problem",
  "Completed Action Plan",
  "Finalized Deployment Need",
  "SOW Signed",
];

export const COMPETITORS: string[] = [
  "Oracle Cloud",
  "On-Prem",
  "Co-location",
  "Akamai",
  "AliCloud",
  "Google Cloud Platform",
  "IBM Softlayer",
  "Microsoft Azure",
  "Other- Cost Optimization",
  "No Competition",
  "*Other",
];

export const MARKETING_SOURCES: string[] = ["Marketing Activity", "None"];
export const MARKETING_CHANNELS: string[] = [
  "AWS Marketing Central",
  "Content Syndication",
  "Display",
  "Email",
  "Live Event",
  "Out Of Home (OOH)",
  "Print",
  "Search",
  "Social",
  "Telemarketing",
  "TV",
  "Video",
  "Virtual Event",
];

export const USE_CASES: string[] = [
  "AI Machine Learning and Analytics",
  "Archiving",
  "Big Data: Data Warehouse / Data Integration / ETL / Data Lake / BI",
  "Blockchain",
  "Business Applications: Mainframe Modernization",
  "Business Applications & Contact Center",
  "Business Applications & SAP Production",
  "Centralized Operations Management",
  "Cloud Management Tools",
  "Cloud Management Tools & DevOps with Continuous Integration & Continuous Delivery (CICD)",
  "Configuration, Compliance & Auditing",
  "Connected Services",
  "Containers & Serverless",
  "Content Delivery & Edge Services",
  "Database",
  "Edge Computing / End User Computing",
  "Energy",
  "Enterprise Governance & Controls",
  "Enterprise Resource Planning",
  "Financial Services",
  "Healthcare and Life Sciences",
  "High Performance Computing",
  "Hybrid Application Platform",
  "Industrial Software",
  "IOT",
  "Manufacturing, Supply Chain and Operations",
  "Media & High performance computing (HPC)",
  "Migration / Database Migration",
  "Monitoring & Observability",
  "Monitoring, logging and performance",
  "Networking",
  "Outpost",
  "SAP",
  "Security & Compliance",
  "Storage & Backup",
  "Training",
  "VMC",
  "VMWare",
  "Web development & DevOps",
];

// Per-opportunity AWS-published quotas. The form enforces these client-side
// and the submit_form_to_ace Lambda re-checks them server-side.
// See https://docs.aws.amazon.com/partner-central/latest/selling-api/quotas.html
export const MAX_AWS_PRODUCTS_PER_OPPORTUNITY = 20;
export const MAX_SOLUTIONS_PER_OPPORTUNITY = 10;

// LifeCycle.Stage enum (7 values). Drives the AWS-side opportunity
// progression. Mirrors ALLOWED_LIFECYCLE_STAGES in src/ace/mapper.py.
// "Launched" and "Closed Lost" are terminal: once set, AWS no longer
// accepts further UpdateOpportunity calls for most fields.
export const LIFECYCLE_STAGES: string[] = [
  "Prospect",
  "Qualified",
  "Technical Validation",
  "Business Validation",
  "Committed",
  "Launched",
  "Closed Lost",
];

// LifeCycle.ClosedLostReason enum (19 values). Only required when
// lifecycle_stage transitions to "Closed Lost". Mirrors
// ALLOWED_CLOSED_LOST_REASONS in src/ace/mapper.py.
export const CLOSED_LOST_REASONS: string[] = [
  "Customer Deficiency",
  "Delay / Cancellation of Project",
  "Legal / Tax / Regulatory",
  "Lost to Competitor - Google",
  "Lost to Competitor - Microsoft",
  "Lost to Competitor - SoftLayer",
  "Lost to Competitor - VMWare",
  "Lost to Competitor - Other",
  "No Opportunity",
  "On Premises Deployment",
  "Partner Gap",
  "Price",
  "Security / Compliance",
  "Technical Limitations",
  "Customer Experience",
  "Other",
  "People/Relationship/Governance",
  "Product/Technology",
  "Financial/Commercial",
];

// Terminal lifecycle stages. Once an opp reaches one of these, the card
// disables the Update button because AWS rejects further mutations.
export const TERMINAL_LIFECYCLE_STAGES: ReadonlySet<string> = new Set([
  "Launched",
  "Closed Lost",
]);

// Source code prefixes for synthetic GovWin IDs (non-GovWin-sourced deals).
export const SOURCE_PREFIXES: Array<{
  label: string;
  value: string;
  description: string;
}> = [
  {
    label: "GovWin (auto)",
    value: "GW",
    description: "Sourced from GovWin IQ; uses the real opportunity id.",
  },
  {
    label: "Customer demo",
    value: "DEMO",
    description: "Customer-facing demo of an internal application.",
  },
  {
    label: "Direct outreach",
    value: "DIRECT",
    description: "Direct outreach, no GovWin lead.",
  },
  {
    label: "AWS-introduced",
    value: "AWS",
    description: "AWS-introduced co-sell.",
  },
  {
    label: "Partner referral",
    value: "REFERRAL",
    description: "Referral from another partner.",
  },
  {
    label: "Event lead",
    value: "EVENT",
    description: "Trade show / conference / event-sourced lead.",
  },
  {
    label: "Inbound web",
    value: "INBOUND",
    description: "Web inbound (lead magnet, contact form).",
  },
];
