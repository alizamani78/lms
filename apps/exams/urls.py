from django.urls import path

from .authoring import ExamCreateView, ExamManageView, PublishView, QuestionsReplaceView
from .bank import AddFromBankView, BankListCreateView
from .grading import EssayListView, GradeAnswerView
from .participation import ParticipationView, ResumeView
from .views import AnswerView, ExamInfoView, ExamListView, ResultView, StartExamView, SubmitView

from .views import BackMoveView
from .authoring import ExamSectionsView
from .entry_cards import EntryCardsView, SeatsView

urlpatterns = [
    path("attempts/<int:pk>/back/", BackMoveView.as_view()),
    path("exams/<int:pk>/sections/", ExamSectionsView.as_view()),
    path("exams/<int:pk>/seats/", SeatsView.as_view()),
    path("exams/<int:pk>/entry-cards/", EntryCardsView.as_view()),
    path("courses/<int:course_id>/exams/", ExamCreateView.as_view()),
    path("exams/<int:pk>/manage/", ExamManageView.as_view()),
    path("exams/<int:pk>/questions/", QuestionsReplaceView.as_view()),
    path("exams/<int:pk>/publish/", PublishView.as_view()),
    path("exams/<int:pk>/add-from-bank/", AddFromBankView.as_view()),
    path("exams/<int:pk>/essays/", EssayListView.as_view()),
    path("exams/<int:pk>/participation/", ParticipationView.as_view()),
    path("exams/<int:pk>/participation/<int:attempt_id>/resume/", ResumeView.as_view()),
    path("answers/<int:pk>/grade/", GradeAnswerView.as_view()),
    path("bank/", BankListCreateView.as_view()),
    path("exams/", ExamListView.as_view()),
    path("exams/<int:pk>/info/", ExamInfoView.as_view()),
    path("exams/<int:pk>/start/", StartExamView.as_view()),
    path("attempts/<int:pk>/result/", ResultView.as_view()),
    path("attempts/<int:pk>/answer/", AnswerView.as_view()),
    path("attempts/<int:pk>/submit/", SubmitView.as_view()),
]
