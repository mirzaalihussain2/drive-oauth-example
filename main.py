import calendar
from dotenv import load_dotenv
import os
from click import prompt
import flask
import requests
import pprint
import datetime

import google.oauth2.credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery
from googleapiclient.errors import HttpError
from typing import TypedDict

load_dotenv()

# The OAuth 2.0 access scope allows for access to the
# authenticated user's account and requires requests to use an SSL connection.
SCOPES = ['https://www.googleapis.com/auth/drive.metadata.readonly',
          'https://www.googleapis.com/auth/calendar.readonly']

app = flask.Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY')

class CalendarEntry(TypedDict):
    id: str
    summary: str
    description: str
    deleted: bool
    hidden: bool
    selected: bool
    timezone: str

def buildCalendarService(credentials: google.oauth2.credentials.Credentials):
    try:
        service = googleapiclient.discovery.build("calendar", "v3", credentials=credentials)
        return service
    except HttpError as error:
        print(f"An error occurred: {error}")

def returnCalendarList(service) -> list[CalendarEntry]:
    page_token = None
    simple_calendar_list = []
    while True:
        calendar_list = service.calendarList().list(pageToken=page_token).execute()
        simple_calendar_list.extend([{
            'id': entry['id'],
            'summary': entry.get('summaryOverride', entry['summary']),
            'description': entry.get('description', ''),
            'deleted': entry.get('deleted', False),
            'hidden': entry.get('hidden', False),
            'selected': entry.get('selected', False),
            'timezone': entry.get('timeZone', '')
        } for entry in calendar_list['items']])
        page_token = calendar_list.get('nextPageToken')
        if not page_token:
            break
    return simple_calendar_list

def getEvents(calendarId: str, service):
    page_token = None
    events_list = []
    while True:
        events = service.events().list(calendarId=calendarId, pageToken=page_token).execute()
        events_list.extend(events['items'])
        page_token = events.get('nextPageToken')
        if not page_token:
            break
    return events_list

def selectCalendar(calendars: list[CalendarEntry]):
    # Display calendars with index numbers
    print("\nAvailable calendars:")
    for i, calendar in enumerate(calendars, 1):
        print(f"{i}. {calendar['summary']}")
    
    # Get user selection
    while True:
        try:
            selection = int(input("\nSelect a calendar (enter the number): "))
            if 1 <= selection <= len(calendars):
                break
            print("Invalid selection. Please try again.")
        except ValueError:
            print("Please enter a valid number.")
    
    # Return the calendar ID for the selected calendar
    selected_calendar = calendars[selection - 1]
    return selected_calendar['id']

@app.route('/')
def index():
    return print_index_table()

@app.route('/drive')
def drive_api_request():
    if 'credentials' not in flask.session:
        return flask.redirect('authorize')
    
    features = flask.session['features']
    
    if features['drive']:
        # Load credentials from the session.
        credentials = google.oauth2.credentials.Credentials(
            **flask.session['credentials']
        )
        
        drive = googleapiclient.discovery.build(
            'drive', 'v3', credentials=credentials
        )

        files = drive.files().list().execute()

        # Save credentials back to session in case access token was refreshed.
        # ACTION ITEM: In a production app, you likely want to save these
        #              credentials in a persistent database instead.
        flask.session['credentials'] = credentials_to_dict(credentials)
        return flask.jsonify(**files)
    
    else:
        # User didn't authorize read-only Drive activity permission.
        # Update UX and application accordingly
        return '<p>Drive feature is not enabled.</p>'

@app.route('/calendar')
def calendar_api_request():
    if 'credentials' not in flask.session:
        return flask.redirect('authorize')

    features = flask.session['features']

    if features['calendar']:
        # Load credentials from the session.
        credentials = google.oauth2.credentials.Credentials(
            **flask.session['credentials']
        )

        service = buildCalendarService(credentials)
        calendars = returnCalendarList(service)
        selelected_calendar_id = selectCalendar(calendars)
        events = getEvents(selelected_calendar_id, service)
        
        event_details = []
        for event in events:
            start = event['start']['dateTime']
            end = event['end']['dateTime']
            # start = event["start"].get("dateTime", event["start"].get("date"))
            # end = event["end"].get("dateTime", event["end"].get("date"))
            duration = None
            if start and end:
                print(f"START TYPE: {type(start)}")
                print(f"START TIME: {start}")
                print(f"END TYPE: {type(end)}")
                print(f"END TIME: {end}")
                
                # start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                # end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
                # duration = str(end_dt - start_dt)
            event_details.append({
                'summary': event.get('summary'),
                'start': start,
                'end': end,
                # 'duration': duration
            })
            
        return flask.jsonify(event_details)

    else:
        # User didn't authorize Calendar read permission.
        # Update UX and application accordingly
        return '<p>Calendar feature is not enabled.</p>'

@app.route('/authorize')
def authorize():
    # Create flow instance to manage the OAuth 2.0 Authorization Grant Flow steps.
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        'client_secret.json', scopes=SCOPES)
    
    # The URI created here must exactly match one of the authorized redirect URIs
    # for the OAuth 2.0 client, which you configured in the API Console. If this
    # value doesn't match an authorized URI, you will get a 'redirect_uri_mismatch'
    # error.
    flow.redirect_uri = flask.url_for('oauth2callback', _external=True)

    authorization_url, state = flow.authorization_url(
        # Enable offline access so that you can refresh an access token without
        # re-prompting the user for permission. Recommended for web server apps.
        access_type='offline',
        # Enable incremental authorization. Recommended as a best practice.
        include_granted_scopes='true'
    )

    # Store the state so the callback can verify the auth server response.
    flask.session['state'] = state
    
    return flask.redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    # Specify the state when creating the flow in the callback so that it can
    # verified in the authorization server response.
    state = flask.session['state']

    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
       'client_secret.json', scopes=SCOPES, state=state)
    flow.redirect_uri = flask.url_for('oauth2callback', _external=True)

    # Use the authorization server's response to fetch the OAuth 2.0 tokens.
    authorization_response = flask.request.url
    flow.fetch_token(authorization_response=authorization_response)

    # Store credentials in the session.
    # ACTION ITEM: In a production app, you likely want to save these
    #              credentials in a persistent database instead.
    credentials = flow.credentials

    credentials = credentials_to_dict(credentials)
    flask.session['credentials'] = credentials
    
    # Check which scopes user granted
    features = check_granted_scopes(credentials)
    flask.session['features'] = features
    return flask.redirect('/')

@app.route('/revoke')
def revoke():
    if 'credentials' not in flask.session:
        return ('You need to <a href="/authorize">authorize</a> before ' +
                'testing the code to revoke credentials.')

    credentials = google.oauth2.credentials.Credentials(
        **flask.session['credentials'])

    revoke = requests.post('https://oauth2.googleapis.com/revoke',
        params={'token': credentials.token},
        headers = {'content-type': 'application/x-www-form-urlencoded'})

    status_code = getattr(revoke, 'status_code')
    if status_code == 200:
        return('Credentials successfully revoked.' + print_index_table())
    else:
        return('An error occurred.' + print_index_table())

@app.route('/clear')
def clear_credentials():
    if 'credentials' in flask.session:
        del flask.session['credentials']
    return ('Credentials have been cleared.<br><br>' +
            print_index_table())

def credentials_to_dict(credentials):
    print("CREDS: ")
    print(credentials.token_uri)
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'granted_scopes': credentials.granted_scopes
    }

def check_granted_scopes(credentials):
    features = {}
    if 'https://www.googleapis.com/auth/drive.metadata.readonly' in credentials['granted_scopes']:
        features['drive'] = True
    else:
        features['drive'] = False
    
    if 'https://www.googleapis.com/auth/calendar.readonly' in credentials['granted_scopes']:
        features['calendar'] = True
    else:
        features['calendar'] = False
    
    return features

def print_index_table():
  return ('<table>' +
          '<tr><td><a href="/calendar">Test an API request</a></td>' +
          '<td>Submit an API request and see a formatted JSON response. ' +
          '    Go through the authorization flow if there are no stored ' +
          '    credentials for the user.</td></tr>' +
          '<tr><td><a href="/authorize">Test the auth flow directly</a></td>' +
          '<td>Go directly to the authorization flow. If there are stored ' +
          '    credentials, you still might not be prompted to reauthorize ' +
          '    the application.</td></tr>' +
          '<tr><td><a href="/revoke">Revoke current credentials</a></td>' +
          '<td>Revoke the access token associated with the current user ' +
          '    session. After revoking credentials, if you go to the test ' +
          '    page, you should see an <code>invalid_grant</code> error.' +
          '</td></tr>' +
          '<tr><td><a href="/clear">Clear Flask session credentials</a></td>' +
          '<td>Clear the access token currently stored in the user session. ' +
          '    After clearing the token, if you <a href="/test">test the ' +
          '    API request</a> again, you should go back to the auth flow.' +
          '</td></tr></table>')

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG') == '1'
    if debug:
        # When running locally, disable OAuthlib's HTTPs verification.
        # ACTION ITEM for developers:
        #     When running in production *do not* leave this option enabled.
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

        # This disables the requested scopes and granted scopes check.
        # If users only grant partial request, the warning would not be thrown.
        os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
    app.run('localhost', 5000, debug=debug)