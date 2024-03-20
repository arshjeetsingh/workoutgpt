from flask import Flask, request, render_template, redirect, url_for, session
import mysql.connector
import requests
import openai
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from datetime import datetime

# Initialize Flask application
app = Flask(__name__)
# Enable debug mode for easier troubleshooting during development
app.config['DEBUG'] = True

# Strava API and OpenAI API credentials
client_id = '120425'
client_secret = ''
openai_api_key = ''

# Set OpenAI API key for usage in the app
openai.api_key = openai_api_key

# Variables for managing Strava access tokens
access_token = None
refresh_token = None
expiration_time = None
auth_url = ''
activities = None


def convert_seconds(total_seconds):
    """Converts seconds into hours, minutes, and seconds."""
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return hours, minutes, seconds

def parse_auth_code(url):
    """Extracts the authorization code from the Strava authorization redirect URL."""
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    scope_values = query_params.get('scope', [None])[0]
    scopes = scope_values.split(',') if scope_values else []
    activity_scope = next((s for s in scopes if 'activity:read_all' in s), None)
    return query_params.get('code', [None])[0] if activity_scope else None

def obtain_tokens(auth_code):
    """Obtains Strava access and refresh tokens using an authorization code."""
    global access_token, refresh_token, expiration_time
    url = 'https://www.strava.com/api/v3/oauth/token'
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'code': auth_code,
        'grant_type': 'authorization_code'
    }
    response = requests.post(url, data=data)
    response_data = response.json()
    if response.status_code == 200:
        access_token = response_data["access_token"]
        refresh_token = response_data["refresh_token"]
        expires_in = response_data["expires_in"]
        expiration_time = datetime.now() + timedelta(seconds=expires_in)
        return access_token, refresh_token
    else:
        raise Exception(f"Error obtaining tokens: {response.text}")

def refresh_strava_access_token_if_needed():
    """Refreshes the Strava access token if it has expired."""
    global access_token, refresh_token, expiration_time
    if not access_token or not expiration_time or datetime.now() >= expiration_time:
        response = requests.post(
            'https://www.strava.com/oauth/token',
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token
            }
        )
        tokens = response.json()
        access_token = tokens['access_token']
        refresh_token = tokens['refresh_token']
        expiration_time = datetime.now() + timedelta(seconds=tokens['expires_in'])

def fetch_strava_activities():
    """Fetches the latest Strava activities for the authorized user."""
    refresh_strava_access_token_if_needed()
    response = requests.get("https://www.strava.com/api/v3/athlete/activities", headers={"Authorization": f"Bearer {access_token}"}, params={'per_page':100, 'page':1})
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception("Failed to fetch Strava activities")

def fetch_strava_profile():
    refresh_strava_access_token_if_needed()
    response = requests.get("https://www.strava.com/api/v3/athlete", headers={"Authorization": f"Bearer {access_token}"})
    if response.status_code == 200:
        profile_data = response.json()
        print("Profile data fetched:", profile_data)  # Debugging line
        return profile_data
    else:
        print(f"Failed to fetch Strava profile: {response.status_code}, {response.text}")
        return None

    
def insert_strava_profile(profile_data):
    if not profile_data:
        print("No profile data provided for insertion.")
        return None

    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        try:
            # Make sure the column names and values match your table schema in MySQL
            cursor.execute("""
                INSERT INTO StravaProfiles (user_id, username, firstname, lastname, city, state, country, sex, premium, created_at, updated_at, badge_type_id, profile_medium, profile, follower_count, friend_count, mutual_friend_count, athlete_type, date_preference, measurement_preference, ftp, weight)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                profile_data.get('id'),
                profile_data.get('username'),
                profile_data.get('firstname'),
                profile_data.get('lastname'),
                profile_data.get('city'),
                profile_data.get('state'),
                profile_data.get('country'),
                profile_data.get('sex'),
                profile_data.get('premium'),
                profile_data.get('badge_type_id'),
                profile_data.get('profile_medium'),
                profile_data.get('profile'),
                profile_data.get('follower_count'),
                profile_data.get('friend_count'),
                profile_data.get('mutual_friend_count'),
                profile_data.get('athlete_type'),
                profile_data.get('date_preference'),
                profile_data.get('measurement_preference'),
                profile_data.get('ftp'),
                profile_data.get('weight'),
            ))
            conn.commit()
        except mysql.connector.Error as err:
            print(f"Error inserting Strava profile into the database: {err}")
            return None
        finally:
            cursor.close()
            conn.close()
        return profile_data.get('id')  # Assuming successful insertion
    else:
        print("Failed to establish database connection.")
        return None


    
def insert_strava_activities(activities, user_id):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        try:
            for activity in activities:
                # Create a unique identifier for the activity; adjust as needed based on your data
                # For example, here we use user_id, start_date, and name to uniquely identify an activity
                unique_id = (user_id, activity.get('start_date'), activity.get('name'))

                # Check if the activity already exists based on the unique identifier
                cursor.execute("""
                    SELECT COUNT(*) FROM StravaActivities 
                    WHERE user_id = %s AND start_date = %s AND name = %s
                """, unique_id)
                result = cursor.fetchone()

                if result[0] > 0:
                    # Activity already exists, skip to the next one
                    continue

                # Prepare data for insertion, ensure this matches your table schema
                data = (
                    user_id,  # Ensure this is the correct user ID
                    activity.get('name', ''),
                    float(activity.get('distance', 0)),
                    activity.get('moving_time', 0),
                    activity.get('elapsed_time', 0),
                    float(activity.get('total_elevation_gain', 0)),
                    activity.get('type', ''),
                    activity.get('start_date', ''),
                    activity.get('start_date_local', ''),
                    activity.get('timezone', ''),
                    activity.get('location_country', ''),
                    activity.get('achievement_count', 0),
                    activity.get('kudos_count', 0),
                    activity.get('comment_count', 0),
                    activity.get('athlete_count', 0),
                    activity.get('photo_count', 0),
                    activity.get('trainer', False),
                    activity.get('commute', False),
                    activity.get('manual', False),
                    activity.get('private', False),
                    activity.get('visibility', ''),
                    activity.get('flagged', False),
                    float(activity.get('average_speed', 0)),
                    float(activity.get('max_speed', 0)),
                    activity.get('has_heartrate', False),
                    activity.get('heartrate_opt_out', False),
                    activity.get('display_hide_heartrate_option', False),
                    float(activity.get('elev_high', 0)),
                    float(activity.get('elev_low', 0)),
                    activity.get('pr_count', 0),
                    activity.get('total_photo_count', 0)
                )

                # Insert the new activity
                cursor.execute("""
                    INSERT INTO StravaActivities (
                        user_id, name, distance, moving_time, elapsed_time, total_elevation_gain,
                        activity_type, start_date, start_date_local, timezone, location_country,
                        achievement_count, kudos_count, comment_count, athlete_count, photo_count,
                        trainer, commute, manual, private, visibility, flagged, average_speed,
                        max_speed, has_heartrate, heartrate_opt_out, display_hide_heartrate_option,
                        elev_high, elev_low, pr_count, total_photo_count
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, data)
                conn.commit()
        except mysql.connector.Error as err:
            print(f"Error inserting Strava activities: {err}")
        finally:
            cursor.close()
            conn.close()


    

def summarize_activities_for_openai(activities):
    """Generates a summary of Strava activities for input into OpenAI's API."""
    summary = "The user's activities include: "
    activities_summary = [f"activity: '{activity['name']}' details: Activity type '{activity['type']}' on {activity['start_date_local'][:10]} for {activity['distance'] / 1000:.2f} km, moving time {convert_seconds(activity['moving_time'])}, elapsed time {convert_seconds(activity['elapsed_time'])}, location {activity['location_country']}, total elevation gain {activity['total_elevation_gain']}" for activity in activities]
    summary += '; '.join(activities_summary) + '.'
    return summary

def preprocess_strava_activities(activities):
    preprocessed_activities = []
    for activity in activities:
        preprocessed_activity = {
            'name': activity.get('name', ''),
            'distance': float(activity.get('distance', 0)),
            'moving_time': int(activity.get('moving_time', 0)),
            'elapsed_time': int(activity.get('elapsed_time', 0)),
            'total_elevation_gain': float(activity.get('total_elevation_gain', 0)),
            'activity_type': activity.get('type', ''),
            'start_date': convert_to_datetime_format(activity.get('start_date')),
            'start_date_local': convert_to_datetime_format(activity.get('start_date_local')),
            'timezone': activity.get('timezone', ''),
            'location_country': activity.get('location_country', ''),
            'achievement_count': int(activity.get('achievement_count', 0)),
            'kudos_count': int(activity.get('kudos_count', 0)),
            'comment_count': int(activity.get('comment_count', 0)),
            'athlete_count': int(activity.get('athlete_count', 0)),
            'photo_count': int(activity.get('photo_count', 0)),
            'trainer': bool(activity.get('trainer', False)),
            'commute': bool(activity.get('commute', False)),
            'manual': bool(activity.get('manual', False)),
            'private': bool(activity.get('private', False)),
            'visibility': activity.get('visibility', ''),
            'flagged': bool(activity.get('flagged', False)),
            'average_speed': float(activity.get('average_speed', 0)),
            'max_speed': float(activity.get('max_speed', 0)),
            'has_heartrate': bool(activity.get('has_heartrate', False)),
            'heartrate_opt_out': bool(activity.get('heartrate_opt_out', False)),
            'display_hide_heartrate_option': bool(activity.get('display_hide_heartrate_option', False)),
            'elev_high': float(activity.get('elev_high', 0)),
            'elev_low': float(activity.get('elev_low', 0)),
            'pr_count': int(activity.get('pr_count', 0)),
            'total_photo_count': int(activity.get('total_photo_count', 0)),
        }
        preprocessed_activities.append(preprocessed_activity)
    return preprocessed_activities

def convert_to_datetime_format(strava_date):
    # Assuming Strava date format is ISO 8601 (YYYY-MM-DDTHH:MM:SSZ), convert to MySQL datetime format
    # Adjust the format as per your requirement
    from datetime import datetime
    if strava_date:
        return datetime.strptime(strava_date, '%Y-%m-%dT%H:%M:%SZ').strftime('%Y-%m-%d %H:%M:%S')
    return None

def ask_openai_about_activities(summary, question):
    """Asks OpenAI for insights on the user's Strava activities based on a provided summary and question."""
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are an AI created to analyze and provide insights on Strava activity data. Make sure that you give precise answers all in accordance to the data provided by the user. Do not change any data. Also, be able to distinguish the activity type for each activity. If no data is provided, and the user asks in accordance to their data, then prompt the user to authorize access to their Strava data. But if the question is general regarding sports, specifically general Strava activities then give response as well, prompting to give access to their data."},
            {"role": "user", "content": summary + " " + question},
        ]
       
    )
    return response.choices[0]['message']['content']

def get_db_connection():
    """Establishes a connection to the MySQL database."""
    try:
        return mysql.connector.connect(
            user='root', password='Root@123', host='localhost', database='myappdb'
        )
    except mysql.connector.Error as err:
        print(f"Database connection error: {err}")
        return None

def insert_message(name, email, message):
    """Inserts a message for a user. If the user does not exist, creates the user."""
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(buffered=True)
        try:
            # Check if user exists
            cursor.execute("SELECT user_id FROM Users WHERE name = %s AND email = %s", (name, email))
            result = cursor.fetchone()

            if result:
                user_id = result[0]
            else:
                # Insert new user
                cursor.execute("INSERT INTO Users (name, email) VALUES (%s, %s)", (name, email))
                user_id = cursor.lastrowid
                conn.commit()

            # Insert new message linked to the user_id
            cursor.execute("INSERT INTO Messages (user_id, message) VALUES (%s, %s)", (user_id, message))
            conn.commit()

        except mysql.connector.Error as err:
            print(f"Error: {err}")
            return None
        finally:
            cursor.close()
            conn.close()
        return user_id


@app.route('/')
def index():
    """Renders the homepage."""
    return render_template('index.html')

@app.route('/query', methods=['POST'])
def query_activities():
    """Handles activity queries by generating and displaying insights from OpenAI."""
    user_question = request.form.get('question', '')
    try:
        activities = fetch_strava_activities()
        summary = summarize_activities_for_openai(activities)
        answer = ask_openai_about_activities(summary, user_question)
        return render_template('index.html', answer=answer)
    except Exception as e:
        print(f"An error occurred: {e}")
        return render_template('index.html', error_message="Failed to process your query.")

@app.route('/exchange_token')
def exchange_token():
    """Handles the exchange of an authorization code for Strava access tokens."""
    auth_code = request.args.get('code')
    if not auth_code:
        return "Authorization failed, code not found", 400
    try:
        access_token, refresh_token = obtain_tokens(auth_code)
        if not access_token or not refresh_token:
            return "Failed to obtain tokens", 400

        profile_data = fetch_strava_profile()
        insert_strava_profile(profile_data)  # Insert profile data into the database
        activities = fetch_strava_activities()
        
        # Use the profile ID from the profile data as the user_id for activities.
        # This assumes the profile ID is the linking field. Adjust if your implementation differs.
        user_id = profile_data['id']  
        preprocessed_activities = preprocess_strava_activities(activities)
        
        insert_strava_activities(preprocessed_activities, user_id)  # Make sure this function accepts the preprocessed activities and a user_id

        # Any additional operations...
        return redirect(url_for('index'))
    except Exception as e:
        print(f"An error occurred: {e}")
        return str(e), 500


@app.route('/submit_form', methods=['POST'])
def submit_form():
    """Handles submission of the contact form."""
    name = request.form.get('name')
    email = request.form.get('email')
    message = request.form.get('message')
    user_id = insert_message(name, email, message)
    if user_id:
        return render_template('index.html', thank_you_message="Thank you for your submission!")
    else:
        return render_template('index.html', error_message="Failed to submit your information.")
    
    
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
