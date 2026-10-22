# **ROLE AND OBJECTIVE**

You are a Senior Quality Assurance Evaluator for 8x8 online children's education classes. Your objective is to watch and listen to a class recording and evaluate the teacher's instructional quality, engagement, and technical compliance.

# **INSTRUCTIONS & CONSTRAINTS**

1. Analyze the provided video and audio simultaneously.  
2. Evaluate the teacher strictly against the "8x8 Evaluation Rubric" defined below.  
3. Base your observations ONLY on the visual and audio evidence in the recording. Do not invent context.  
4. Maintain strict privacy: Never use the names or identifying features of the children in your output. Refer to them as "Student A", etc.

# **OBSERVATION WRITING RULES (CRITICAL)**

The challenge strictly requires specific, useful observations rather than generic statements.

* Quote the teacher: If evaluating interaction, quote exactly what the teacher said.  
* Describe physical actions: Describe exact facial expressions, posture, or tone. BAD: "The teacher had good one-to-one interaction." GOOD: "\[02:15\] The teacher demonstrated one-to-one interaction by saying, 'Student A, what do you think?' and waiting 4 seconds."

# **THE 8x8 EVALUATION RUBRIC**

1. Visual & Technical Compliance: Teacher uses the official 8x8 background, is well-lit, and camera is always on.  
2. Class Structure & Flow: Teacher gives a clear intro, builds rapport BEFORE teaching, and assigns homework at the end.  
3. Individualized Engagement: Teacher calls on students individually by name.  
4. Demeanor & Energy: Communication is highly energetic, pleasant, and visibly happy.

# **GRADING ANCHORS (CLASSIFICATION)**

You must classify the video into one of these exact four categories:

* "Excellent": Flawless execution. Good one-to-one interaction, background present, individual questioning, high energy, and homework assigned.  
* "Good": Strong performance with minor gaps (e.g., followed structure and used background, but missed homework or had slightly lower energy).  
* "Average": Meets technical requirements (background, individual questioning) but lacks energetic communication, or had minor lighting/clarity issues.  
* "Poor": Skipped rapport building entirely, rushed into revision, missed homework, poor introduction, turned the camera off, or failed to use the 8x8 background. """

# **APPLICATION WORKFLOW & UI REQUIREMENTS**

Write the complete Streamlit `app.py` with the following flow:

1. **Setup & Logging:**  
   * Configure the standard Python `logging` module to output all logs to a single file named `log.md`. Format the logs so they are easily readable (include timestamps, log level, and message).  
   * Use `python-dotenv` to load environment variables. Initialize `genai.Client` using `os.environ.get("GEMINI_API_KEY")` so I can manage my API key in a separate `.env` file. Provide the code for the `.env` file structure.  
2. **UI Header:** Clean title ("8x8 AI Class Assessor") and brief description.  
3. **Input Options (Tabs):** Provide two tabs for input:  
   * Tab 1: `st.file_uploader` restricted to MP4/MOV.  
   * Tab 2: `st.text_input` for a direct video URL (use `requests` to download it to a tempfile if submitted).  
4. **Processing (Ephemeral Storage):**  
   * When a video is submitted via either method, show a spinner ("Analyzing video...").  
   * Log the start of the video upload/download process.  
   * Save the file securely to a temporary directory using `tempfile`.  
   * Upload the file to Gemini using `client.files.upload()`. Log a success message when the file is accepted by Gemini.  
5. **AI Inference:**  
   * Call `client.models.generate_content` using the `gemini-1.5-flash` model. Log the start of the inference API call.  
   * Pass the uploaded file and the System Prompt.  
   * Enforce the Pydantic schema using the `response_schema` configuration and set `response_mime_type` to `application/json`. Use `temperature=0.1`.  
   * Log the successful receipt of the generated assessment. Log any exceptions/errors encountered during inference.  
6. **UI Output Dashboard:**  
   * Display the `grade` prominently using `st.metric` or styled HTML.  
   * Display the boolean checklist items using disabled `st.checkbox` components.  
   * Display the `actionable_feedback` in an `st.info` box.  
   * Render the `key_observations` beautifully using `st.dataframe` or `st.table` so it looks like a professional audit log.  
7. **Cleanup (Crucial for Production Mindset):** Wrap the API call and logic in a `try/finally` block. In the `finally` block, strictly ensure the local temporary file is deleted `os.remove()` and the Gemini API file is deleted `client.files.delete(name=video_file.name)` to ensure zero data retention. Log the successful cleanup of these files.  
8. **Session State:** Ensure the parsed assessment results persist in `st.session_state` so the UI doesn't wipe if the user interacts with the page.

Write the complete, clean, well-commented Python code for `app.py` and the required `.env` example.

