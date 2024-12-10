import os
import csv
import random
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from waitress import serve
import requests
import socket
from datetime import timedelta

app = Flask(__name__)
app.secret_key = "your_secret_key"
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.permanent_session_lifetime = timedelta(weeks=52)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

class Deck(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(250), nullable=True)
    user_id = db.Column(db.Integer, nullable=False)

class Flashcard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    deck_id = db.Column(db.Integer, db.ForeignKey('deck.id'), nullable=False)
    front = db.Column(db.String(500), nullable=False)  # Text or image path
    back = db.Column(db.String(500), nullable=False)  # Text or image path

    def to_dict(self):
        return {
            'id': self.id,
            'front': self.front,
            'back': self.back,
            'deck_id': self.deck_id
        }

# Home and account management routes
@app.route('/')
def home():
    if 'user_id' in session:
        # Fetch decks for the logged-in user
        decks = Deck.query.filter_by(user_id=session['user_id']).all()
    else:
        decks = []

    return render_template('home.html', decks=decks)


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('signup'))
        
        # Hash the password and create the new user
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()

        # Automatically log in the user
        session['user_id'] = new_user.id
        session.permanent = True  # Make the session permanent
        flash('', 'clear')
        return redirect(url_for('home'))
    
    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session.permanent = True  # Make the session permanent
            flash('', 'clear')
            return redirect(url_for('home'))
        flash('Invalid username or password.', 'danger')
        return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('home'))

# Deck Management Routes
@app.route('/decks')
def view_decks():
    if 'user_id' not in session:
        flash('Please log in to view your decks.', 'danger')
        return redirect(url_for('login'))
    decks = Deck.query.filter_by(user_id=session['user_id']).all()
    return render_template('decks.html', decks=decks, show_navbar=False)

@app.route('/decks/create', methods=['GET', 'POST'])
def create_deck():
    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        new_deck = Deck(name=name, description=description, user_id=session['user_id'])
        db.session.add(new_deck)
        db.session.commit()
        return redirect(url_for('view_decks'))
    return render_template('create_deck.html')

@app.route('/decks/delete/<int:deck_id>')
def delete_deck(deck_id):
    deck = Deck.query.get_or_404(deck_id)
    if deck.user_id != session['user_id']:
        flash('You do not have permission to delete this deck.', 'danger')
        return redirect(url_for('view_decks'))
    Flashcard.query.filter_by(deck_id=deck.id).delete()
    db.session.delete(deck)
    db.session.commit()
    return redirect(url_for('view_decks'))

# Flashcard Management Routes
@app.route('/decks/<int:deck_id>/flashcards', methods=['GET', 'POST'])
def manage_flashcards(deck_id):
    deck = Deck.query.get_or_404(deck_id)
    if deck.user_id != session['user_id']:
        flash('You do not have permission to access this deck.', 'danger')
        return redirect(url_for('view_decks'))

    if request.method == 'POST':
        front = request.form['front']
        back = request.form['back']
        new_flashcard = Flashcard(deck_id=deck_id, front=front, back=back)
        db.session.add(new_flashcard)
        db.session.commit()

    flashcards = Flashcard.query.filter_by(deck_id=deck_id).all()
    return render_template('manage_flashcards.html', deck=deck, flashcards=flashcards)

@app.route('/flashcards/<int:flashcard_id>/delete', methods=['POST'])
def delete_flashcard(flashcard_id):
    flashcard = Flashcard.query.get_or_404(flashcard_id)

    # Ensure that the flashcard belongs to the current user (optional)
    deck = Deck.query.get(flashcard.deck_id)
    if deck and deck.user_id != session['user_id']:
        flash('You do not have permission to delete this flashcard.', 'danger')
        return redirect(url_for('view_decks', deck_id=deck.id))

    # Delete the flashcard from the database
    db.session.delete(flashcard)
    db.session.commit()  # Make sure to commit the changes


    # Redirect to the flashcard management page of the deck
    return redirect(url_for('manage_flashcards', deck_id=deck.id))



@app.route('/decks/<int:deck_id>/import', methods=['POST'])
def import_csv(deck_id):
    deck = Deck.query.get_or_404(deck_id)
    if deck.user_id != session['user_id']:
        flash('You do not have permission to import into this deck.', 'danger')
        return redirect(url_for('view_decks'))

    file = request.files['file']
    if file and file.filename.endswith('.csv'):
        # Open the file with UTF-8 encoding to handle special characters
        try:
            file_stream = file.stream.read().decode('utf-8')  # Decode stream to text using UTF-8
            csv_reader = csv.reader(file_stream.splitlines(), delimiter=',')
            next(csv_reader)  # Skip the header

            for row in csv_reader:
                if len(row) == 2:  # Ensure we have two columns (Front and Back)
                    front, back = row
                    new_flashcard = Flashcard(deck_id=deck_id, front=front, back=back)
                    db.session.add(new_flashcard)

            db.session.commit()

        except UnicodeDecodeError:
            flash('There was an error decoding the file. Please ensure it is in UTF-8 encoding.', 'danger')

    return redirect(url_for('view_decks', deck_id=deck_id))

@app.route('/decks/<int:deck_id>/export')
def export_csv(deck_id):
    deck = Deck.query.get_or_404(deck_id)
    if deck.user_id != session['user_id']:
        flash('You do not have permission to export this deck.', 'danger')
        return redirect(url_for('view_decks'))

    flashcards = Flashcard.query.filter_by(deck_id=deck_id).all()
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f'deck_{deck_id}.csv')

    # Open the file with UTF-8 encoding to handle special characters
    with open(file_path, 'w', newline='', encoding='utf-8') as csv_file:
        csv_writer = csv.writer(csv_file)
        # Write the header (optional, but recommended)
        csv_writer.writerow(['Front', 'Back'])
        for flashcard in flashcards:
            csv_writer.writerow([flashcard.front, flashcard.back])

    return send_file(file_path, as_attachment=True)

@app.route('/decks/<int:deck_id>/practice/settings', methods=['GET', 'POST'])
def practice_settings(deck_id):
    # Ensure the deck exists
    deck = Deck.query.get_or_404(deck_id)

    # If the user is logged in, we proceed
    if 'user_id' not in session:
        flash('Please log in to practice your decks.', 'danger')
        return redirect(url_for('login'))

    if request.method == 'POST':
        # Collect practice settings from the form
        side_first = request.form.get('side_first', 'front_first')  # Default to 'front_first'
        random_order = 'random_order' in request.form
        
        # Store the settings in the session or pass them to the practice page
        session['practice_settings'] = {
            'deck_id': deck.id,
            'side_first': side_first,  # Save 'side_first' in the session
            'random_order': random_order
        }

        # Redirect to the practice page
        return redirect(url_for('practice', deck_id=deck.id))

    return render_template('practice_settings.html', deck=deck)

@app.route('/decks/<int:deck_id>/practice', methods=['GET'])
def practice(deck_id):
    # Ensure the deck exists
    deck = Deck.query.get_or_404(deck_id)

    # Ensure the user is logged in
    if 'user_id' not in session:
        flash('Please log in to practice your decks.', 'danger')
        return redirect(url_for('login'))

    # Retrieve the user's practice settings from session
    practice_settings = session.get('practice_settings', {})

    if practice_settings.get('deck_id') != deck.id:
        flash('Invalid practice settings.', 'danger')
        return redirect(url_for('view_decks'))

    # Fetch the flashcards for the deck
    flashcards = Flashcard.query.filter_by(deck_id=deck.id).all()

    if practice_settings.get('random_order'):
        flashcards = random.sample(flashcards, len(flashcards))

    # Convert flashcards to dictionaries
    flashcards_dict = [flashcard.to_dict() for flashcard in flashcards]

    # Determine the practice order (front_first or back_first)
    side_first = practice_settings.get('side_first', 'front_first')  # Default to 'front_first'

    return render_template('practice.html', deck=deck, flashcards=flashcards_dict, side_first=side_first)


def get_public_ip():
    try:
        response = requests.get('https://api.ipify.org')
        if response.status_code == 200:
            return response.text
        else:
            return "Failed to retrieve IP address"
    except Exception as e:
        return f"An error occurred: {str(e)}"
    
def get_private_ip():
    try:
        private_ip = socket.gethostbyname(socket.gethostname())
        return private_ip
    except Exception as e:
        return f"An error occurred: {str(e)}"

# This is what actually runs the server and tells it where to host it. It also makes sure we have a database and if not it makes a new one
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    print("Over your local wifi app is running on " + get_private_ip() + ":5000")
    print("App is running on " + get_public_ip() + ":5000 over the internet assuming you set up port forwarding")
    serve(app, host='0.0.0.0', port=5000, threads=8)
