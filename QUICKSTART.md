# Clinical Data Explorer - Quick Start Guide

## Get Started in 4 Steps

### Step 1: Install Dependencies

Some dataset related features require dependencies that only exist in Domino execution. To develop in a Domino Workspace,
- Create a Git-Based Project with this repo
- In the central config dashboard, set `com.cerebro.domino.workbench.workspace.sandboxForwardedPortsInVsCode=false`
- Launch a vscode workspace
- Set `MAIN_APP_PORT=8000`
- Follow the next instructions to install and run the app.
- Then open the vscode proxied port for the flask app via the popup provided by vscode
-
**install required tools**
- [uv](https://docs.astral.sh/uv/)

**install app dependencies**
```bash
uv sync --locked
```

### Step 2: Configure LLM (Optional for Chat Feature)

The Chat tab requires an LLM provider. Set these environment variables:

```bash
# For OpenAI (default)
export LLM_API_KEY="sk-your-api-key"
export LLM_MODEL="gpt-4o"  # optional, defaults to gpt-4o-mini

# For Local Ollama
export LLM_BASE_URL="http://localhost:11434/v1"
export LLM_MODEL="llama3"

# For Other OpenAI-Compatible Providers (Together AI, etc.)
export LLM_BASE_URL="https://api.together.xyz/v1"
export LLM_API_KEY="your-api-key"
export LLM_MODEL="meta-llama/Llama-3-70b-chat-hf"
```

**Note:** The app will run without LLM configuration - the Table and Explore tabs work without it. The Chat tab will show setup instructions if not configured.

### Step 3: Start the Servers

**Option A: Use the startup script (Recommended)**
```bash
./start_servers.sh
```

**Option B: Start manually**

Terminal 1 - Start MCP Server:
```bash
uv run --locked python data_analysis_mcp.py
```

Terminal 2 - Start Flask App:
```bash
uv run --locked python app.py
```

### Step 4: Open Your Browser
Navigate to: http://localhost:5000

## Using the Interface

1. **Select Dataset**: Choose a CSV file from the dropdown menu
2. **Load Dataset**: Click the "Load" button
3. **Ask Questions**: Start chatting with your data!

## Example Questions to Try

### General Information
- "What columns are in this dataset?"
- "How many rows does this dataset have?"
- "Show me the first 10 rows"
- "What are the numeric and categorical columns?"

### Statistics
- "Show me statistics for all numeric features"
- "What's the mean and median of [column_name]?"
- "Are there any missing values?"
- "Show me the distribution of [column_name]"

### Correlations
- "What are the correlations between features?"
- "Which features are most correlated with [column_name]?"
- "Show me the correlation matrix"

### Comparisons
- "Compare [feature1] and [feature2]"
- "What's the relationship between [feature1] and [feature2]?"
- "Group [feature] by [category]"

### Specific Examples for Included Datasets

**For diabetes_dataset.csv:**
- "What attributes have the strongest correlation with is_diabetic?"
- "Show me the average weight for diabetic vs non-diabetic people"
- "Compare calories_wk and hrs_exercise_wk"
- "What's the correlation between exercise_intensity and weight?"

**For earthquake_data_tsunami.csv:**
- "What's the average magnitude of earthquakes that cause tsunamis?"
- "Show me the correlation between magnitude and depth"
- "Compare the distribution of earthquakes by year"
- "What's the relationship between magnitude and tsunami occurrence?"

## Testing

Run the test suite to verify everything works:
```bash
uv run --locked playwright install chromium  # one-time setup for e2e
make test-all
```

This will test:
- MCP contract coverage
- End-to-end browser smoke coverage

## Troubleshooting

### "No datasets found"
**Solution**: Add CSV files to the `datasets/` folder

### "Could not connect to MCP server"
**Solution**: Make sure the MCP server is running on port 8888
```bash
uv run --locked python data_analysis_mcp.py
```

### "No dataset loaded"
**Solution**: Click the "Load" button after selecting a dataset

### Import errors
**Solution**: Install all dependencies
```bash
uv sync --locked
```

### Port already in use
**Solution**: Kill the process using the port or change the port in the code
```bash
# Find process using port 8888
lsof -i :8888
# Kill it
kill -9 <PID>
```

## Adding Your Own Datasets

1. Place your CSV file in the `datasets/` folder
2. Refresh the web page or restart the servers
3. Select your dataset from the dropdown
4. Click "Load" and start analyzing!

**Requirements:**
- File must be in CSV format
- File must have a header row with column names
- File must be in the `datasets/` folder

## What Makes This Special?

- **No Coding Required**: Ask questions in natural language
- **Works with Any Data**: Automatically adapts to your dataset structure
- **Instant Insights**: Get statistics, correlations, and comparisons immediately
- **Multiple Datasets**: Switch between different datasets easily
- **Flexible LLM Support**: Works with OpenAI, Ollama, Together AI, and other OpenAI-compatible providers

## More Information

- **Full Documentation**: See `README.md`
- **Change Log**: See `CHANGES.md`
- **API Reference**: Visit http://localhost:8888/docs when MCP server is running

## Need Help?

Common issues and solutions:
1. **Servers won't start**: Check if ports 5000 and 8888 are available
2. **Chat not working**: Check your LLM configuration (see Step 2 above)
   - For OpenAI: `export LLM_API_KEY='sk-your-key'`
   - For Ollama: `export LLM_BASE_URL='http://localhost:11434/v1' LLM_MODEL='llama3'`
3. **Dataset won't load**: Check file format and location
4. **Empty responses**: Try more specific questions

## You're Ready!

Start exploring your data with natural language queries. The AI will handle the technical details!

---

**Pro Tip**: The more specific your question, the better the answer. Include column names and specific metrics when possible.
