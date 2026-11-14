// Worked examples for the demo: one click opens the drawing and asks the question.
// Every one is a drawing of the development split. `state` is what the system returned
// when the list was written; tests/test_server.py asks each one again and fails if a
// stop no longer returns that state.

export interface Stop {
  label: string;
  note: string;
  state: "verified" | "check" | "declined";
  drawing: string;
  question: string;
  view?: "plan" | "3d" | "both";
  card?: boolean;  // shown as a card on the home page
}

export const TOUR: Stop[] = [
  {
    label: "Count every window on a floor",
    card: true,
    note: "Geometry · 12 marked on the plan and in the model · free",
    state: "verified",
    drawing: "data/swiss_dwellings/site2150_bldg3001_floor13690",
    question: "How many windows are drawn on this floor plan?",
  },
  {
    label: "Doors, and a door drawn twice",
    card: true,
    note: "32 outlines in the file, 23 distinct doors: it says so, and asks for a look",
    state: "check",
    drawing: "data/swiss_dwellings/site2150_bldg3001_floor13690",
    question: "How many doors (interior doors and entrance doors together) are drawn on this floor plan?",
  },
  {
    label: "Net area of one apartment",
    card: true,
    note: "Rooms fill with their m² · balconies hatched out · model alone said 75.2",
    state: "verified",
    drawing: "data/swiss_dwellings/site3764_bldg5789_floor24598",
    question: "What is the net floor area of apartment B in m², summing all its rooms except balconies, loggias, terraces and other outdoor spaces?",
  },
  {
    label: "Asked in German, shown in 3D",
    note: "Plan raised from the DXF · model alone said 69.5",
    state: "verified",
    drawing: "data/swiss_dwellings/site383_bldg846_floor3730",
    question: "Wie gross ist die Nettowohnfläche von Wohnung C in m² (Summe aller Räume ohne Balkone, Loggien, Terrassen und andere Aussenräume)?",
    view: "both",
  },
  {
    label: "A room with no name on the drawing",
    note: "Right answer, but one room is unnamed, so it asks for a look",
    state: "check",
    drawing: "data/swiss_dwellings/site1000_bldg2082_floor9385",
    question: "Wie gross ist die Nettowohnfläche von Wohnung B in m² (Summe aller Räume ohne Balkone, Loggien, Terrassen und andere Aussenräume)?",
  },
  {
    label: "Doors on one storey of a building model",
    card: true,
    note: "IFC query · 42 doors lit in 3D · free",
    state: "check",
    drawing: "data/ifc_models/bsc-esplanades_1807-ep-ar-v18_plan-04-4-korrus",
    question: "How many doors are shown on the floor plan of '4.korrus'?",
    view: "both",
  },
  {
    label: "One room's area, asked in Estonian",
    card: true,
    note: "IFC query · two geometry sources agree · model alone said 3.4",
    state: "check",
    drawing: "data/ifc_models/bsc-esplanades_1807-ep-ar-v18_plan-04-4-korrus",
    question: "Kui suur on ruumi „TUALETTRUUM (47-3)“ netopõrandapind korrusel „4.korrus“ (m²)?",
    view: "both",
  },
  {
    label: "Bedrooms on a scan, read from its labels",
    card: true,
    note: "Local OCR · two printed sources agree · free",
    state: "check",
    drawing: "data/cubicasa5k/high_quality_architectural-14441",
    question: "How many bedrooms (makuuhuone, usually marked MH) are on this floor plan?",
  },
  {
    label: "Windows on a scanned Finnish plan",
    note: "No vector data: Gemini boxes each window, the code counts",
    state: "check",
    drawing: "data/cubicasa5k/high_quality_architectural-6081",
    question: "How many windows are shown on this floor plan?",
  },
  {
    label: "A scan where the model miscounts",
    note: "23 boxes where the dataset says 20: why pixel answers are never Verified",
    state: "check",
    drawing: "data/cubicasa5k/high_quality_architectural-504",
    question: "How many windows are shown on this floor plan?",
  },
  {
    label: "A question the plan cannot answer",
    card: true,
    note: "A 2D plan holds no heights, so there is no number to give",
    state: "declined",
    drawing: "data/swiss_dwellings/site2150_bldg3001_floor13690",
    question: "What is the ceiling height of room 2.21?",
  },
];
