# Config Authentication

Step 1: Clone the .env.template and rename to .env
Step 2: Run command
```
chainlit create-secret 
```
Step 3: Copy the chainlit secret into .env and update other environment variables

# Config Data Persistent

Step 1: Update POSTGRES_xxx variables in .env file correctly
Step 2: Start postgres container by using command
```
docker-compose up -d
```
Step 3: Restart server to apply tables to database
